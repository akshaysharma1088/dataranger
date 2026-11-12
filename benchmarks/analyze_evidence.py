"""Two analyses on the main incident records, same splits as analyze_repair.py.

1. Decomposition of every automated decision at each target: repairs applied (right / wrong),
   alerts declared benign with `none` (right / wrong) and escalations, plus harm among automated
   decisions and correct-repair coverage. Table 3 reports automation and harm per incident; this
   makes the denominators explicit.

2. Evidence ablation: the same candidates, the same calibration splits and the same Learn-then-Test
   procedure, with different scores deciding which candidate is selected and whether it is applied.
     residual            per-channel gap to the source's control totals (DataRanger)
     residual_partition  partition-level gap only
     invariants          no source at all: declared invariants, then the smallest change
     monitors            the team's monitors re-run on the fixed partition (quiet or not)
   The question is whether external evidence buys anything beyond generic risk calibration.
Writes results/evidence_summary.json.
"""
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from dataranger.repair.gate import GRID, calibrate, decide, evaluate, hb_pvalue, loss

DECOYS = {"volume_spike", "big_ticket_day", "false_alarm"}
DELTA, SPLITS = 0.05, 100


def load(path):
    src = Path(path)
    return [json.loads(l) for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt"))]


def make_splits(recs, key):
    by_inst = defaultdict(list)
    for r in recs:
        by_inst[key(r)].append(r)
    keys = sorted(by_inst)
    rng = np.random.default_rng(0)
    out = []
    for _ in range(SPLITS):
        perm = rng.permutation(len(keys))
        cal_k = {keys[i] for i in perm[: len(keys) // 2]}
        out.append(([r for k in keys if k in cal_k for r in by_inst[k]], [r for k in keys if k not in cal_k for r in by_inst[k]]))
    return out


def is_real(r):
    return any(k not in DECOYS for k in r["kinds"])


def decompose(test, lam, res="residual"):
    recs = [r for r in test if not r["table_level"]]
    n = len(recs)
    c = defaultdict(int)
    for r in recs:
        a = decide(r, "verified", lam, res)
        if a is None:
            c["escalated"] += 1
            if is_real(r):
                c["escalated_real"] += 1
        elif a["action"] == "none":
            c["benign_right" if a["correct"] else "benign_wrong"] += 1
        else:
            c["repair_right" if a["correct"] else "repair_wrong"] += 1
    auto = n - c["escalated"]
    real = sum(is_real(r) for r in recs)
    return {"incidents": n, "automated": auto / n, "repairs_right": c["repair_right"] / n, "repairs_wrong": c["repair_wrong"] / n,
            "benign_right": c["benign_right"] / n, "benign_wrong": c["benign_wrong"] / n, "escalated": c["escalated"] / n,
            "harm": (c["repair_wrong"] + c["benign_wrong"]) / n,
            "harm_among_automated": (c["repair_wrong"] + c["benign_wrong"]) / max(auto, 1),
            "real_errors_repaired": c["repair_right"] / max(real, 1), "real_errors_escalated": c["escalated_real"] / max(real, 1),
            "real_share": real / n}


# --- generic scores -------------------------------------------------------------------------
def score_of(c, kind):
    if kind in ("residual", "residual_partition"):
        return c[kind]
    if kind == "invariants":  # no source: a candidate is only as good as the invariants it keeps
        return 0.0 if c["invariants"] else float("inf")
    if kind == "monitors":
        return 0.0 if (c["invariants"] and c["monitors_quiet"]) else float("inf")
    raise ValueError(kind)


def select_generic(r, kind):
    cands = r["candidates"]
    if not cands:
        return None
    return min(cands, key=lambda c: (round(score_of(c, kind), 9), c["changed"], c["action"] != "none"))


def decide_generic(r, kind, lam):
    if r["table_level"]:
        return None
    c = select_generic(r, kind)
    return c if c and c["tier"] == "auto" and score_of(c, kind) <= lam else None


def calibrate_generic(records, kind, alpha, grid=GRID):
    recs = [r for r in records if not r["table_level"]]
    n, best = len(recs), -1.0
    for lam in grid:
        risk = float(np.mean([loss(r, decide_generic(r, kind, lam)) for r in recs]))
        if hb_pvalue(risk, n, alpha) <= DELTA:
            best = float(lam)
        else:
            break
    return best


def evaluate_generic(records, kind, lam):
    recs = [r for r in records if not r["table_level"]]
    applied = [decide_generic(r, kind, lam) for r in recs]
    harm = [loss(r, a) for r, a in zip(recs, applied)]
    real = [is_real(r) for r in recs]
    return {"auto_rate": float(np.mean([a is not None for a in applied])), "harm": float(np.mean(harm)),
            "real_errors_fixed": float(sum(a is not None and a["correct"] and a["action"] != "none" and x for a, x in zip(applied, real)) / max(sum(real), 1))}


# --- learned scores: a proposer-side confidence model, with and without the source ----------------
from sklearn.ensemble import HistGradientBoostingClassifier

ACTIONS = ["none", "dedupe", "dedupe_business", "reload", "rescale", "quarantine", "trim", "trim_value"]
DETECTORS = ["duplicate_keys", "duplicate_rows", "missing_partition", "orphan_keys", "volume_drop", "volume_rise", "mix_drop",
             "mix_rise", "scale_shift", "reconcile_gap"]
TAUS = np.round(np.arange(1.0, -0.001, -0.01), 2)


def feats(r, c, with_source):
    # without the source, the reconciliation monitor (which reads it) is not among the alerts the model sees
    dets = DETECTORS if with_source else [d for d in DETECTORS if d != "reconcile_gap"]
    x = [float(c["action"] == a) for a in ACTIONS] + [float(d in r["detectors"]) for d in dets]
    x += [float(c["changed"]), float(c["invariants"]), float(c["monitors_quiet"]), float(r["reload_ok"]), float(len(r["candidates"])),
          float(c["tier"] == "auto")]
    if with_source:
        x += [min(c["residual"], 10.0), min(c["residual_partition"], 10.0)]
    return x


def learned_split(cal, test, alpha, with_source, seed):
    """Fit a score on half of the calibration instances, certify a threshold on the other half, evaluate on test."""
    inst = sorted({(r["size"], r["seed"]) for r in cal})
    rng = np.random.default_rng(seed)
    rng.shuffle(inst)
    learn_k = set(inst[: len(inst) // 2])
    learn = [r for r in cal if (r["size"], r["seed"]) in learn_k and not r["table_level"]]
    tune = [r for r in cal if (r["size"], r["seed"]) not in learn_k and not r["table_level"]]
    X = [feats(r, c, with_source) for r in learn for c in r["candidates"]]
    y = [int(c["correct"]) for r in learn for c in r["candidates"]]
    clf = HistGradientBoostingClassifier(max_iter=200, random_state=seed).fit(X, y)

    def bests(recs):
        """Best auto-tier candidate per record by model score, predicted in one batch."""
        rows, owner = [], []
        for i, r in enumerate(recs):
            for c in r["candidates"]:
                if c["tier"] == "auto":
                    rows.append(feats(r, c, with_source))
                    owner.append((i, c))
        p = clf.predict_proba(rows)[:, 1] if rows else np.array([])
        out = [(None, 0.0)] * len(recs)
        for (i, c), pi in zip(owner, p):
            if pi > out[i][1] or out[i][0] is None:
                out[i] = (c, float(pi))
        return list(zip(recs, out))

    def risk_at(recs, tau):
        return float(np.mean([loss(r, b) if (b is not None and p >= tau) else 0 for r, (b, p) in recs]))

    tune_b = bests(tune)
    n, tau_hat = len(tune), None
    for tau in TAUS:
        if hb_pvalue(risk_at(tune_b, tau), n, alpha) <= DELTA:
            tau_hat = tau
        else:
            break
    test_r = [r for r in test if not r["table_level"]]
    test_b = bests(test_r)
    applied = [b if (tau_hat is not None and b is not None and p >= tau_hat) else None for r, (b, p) in test_b]
    harm = [loss(r, a) for r, a in zip(test_r, applied)]
    real = [is_real(r) for r in test_r]
    return {"auto_rate": float(np.mean([a is not None for a in applied])), "harm": float(np.mean(harm)),
            "real_errors_fixed": float(sum(a is not None and a["correct"] and a["action"] != "none" and x for a, x in zip(applied, real)) / max(sum(real), 1)),
            "certified": float(tau_hat is not None)}


if __name__ == "__main__":
    import sys
    only_learned = len(sys.argv) > 1 and sys.argv[1] == "learned"
    out = json.load(open("results/evidence_summary.json")) if only_learned else {}
    for name, path, key in [("synthetic", "results/repair_records.jsonl", lambda r: (r["size"], r["seed"])),
                            ("tpcds", "results/tpcds_records.jsonl", lambda r: r["window"] if "window" in r else (r["size"], r["seed"]))]:
        recs = load(path)
        splits = make_splits(recs, key)
        if only_learned:
            if name != "synthetic":
                continue
            abl = out[name]["evidence_ablation"]
            for with_source in (False, True):
                for alpha in (0.01, 0.02, 0.05):
                    rows = [learned_split(cal, test, alpha, with_source, i) for i, (cal, test) in enumerate(splits[:20])]
                    abl[f"learned_{'with' if with_source else 'no'}_source|{alpha}"] = {k: float(np.mean([x[k] for x in rows])) for k in rows[0]}
                    print(name, with_source, alpha, abl[f"learned_{'with' if with_source else 'no'}_source|{alpha}"], flush=True)
            json.dump(out, open("results/evidence_summary.json", "w"), indent=1)
            continue
        dec = {}
        for alpha in (0.01, 0.02, 0.05):
            rows = [decompose(test, calibrate(cal, alpha, DELTA)) for cal, test in splits]
            dec[str(alpha)] = {k: float(np.mean([x[k] for x in rows])) for k in rows[0]}
        # uncalibrated baselines, same decomposition, over all incidents
        allinc = [r for r in recs if not r["table_level"]]
        base = {}
        for pol in ("rules", "monitors"):
            c = defaultdict(int)
            for r in allinc:
                a = decide(r, pol)
                if a is None:
                    c["escalated"] += 1
                elif a["action"] == "none":
                    c["benign_right" if a["correct"] else "benign_wrong"] += 1
                else:
                    c["repair_right" if a["correct"] else "repair_wrong"] += 1
            n = len(allinc)
            base[pol] = {k: v / n for k, v in c.items()}
        abl = {}
        for kind in ("residual", "residual_partition", "invariants", "monitors"):
            grid = GRID if kind.startswith("residual") else np.array([0.0])
            for alpha in (0.01, 0.02, 0.05):
                rows = []
                for cal, test in splits:
                    lam = calibrate_generic(cal, kind, alpha, grid)
                    rows.append({**evaluate_generic(test, kind, lam), "certified": lam >= 0})
                abl[f"{kind}|{alpha}"] = {k: float(np.mean([x[k] for x in rows])) for k in rows[0]}
        if name == "synthetic":
            for with_source in (False, True):
                for alpha in (0.01, 0.02, 0.05):
                    rows = [learned_split(cal, test, alpha, with_source, i) for i, (cal, test) in enumerate(splits[:20])]
                    abl[f"learned_{'with' if with_source else 'no'}_source|{alpha}"] = {k: float(np.mean([x[k] for x in rows])) for k in rows[0]}
        out[name] = {"decomposition": dec, "baselines": base, "evidence_ablation": abl}
        print(f"\n== {name}")
        for a, d in dec.items():
            print(f"  alpha {a}: auto {d['automated']:.3f} repairs {d['repairs_right']:.3f}/{d['repairs_wrong']:.4f} benign {d['benign_right']:.3f}/{d['benign_wrong']:.4f} "
                  f"esc {d['escalated']:.3f} harm|auto {d['harm_among_automated']:.4f} real repaired {d['real_errors_repaired']:.3f} real escalated {d['real_errors_escalated']:.3f}")
        for k, v in abl.items():
            print(f"  {k:26s} auto {v['auto_rate']:.3f} harm {v['harm']:.4f} fixed {v['real_errors_fixed']:.3f} certified {v['certified']:.2f}")
    json.dump(out, open("results/evidence_summary.json", "w"), indent=1)
