"""Two controlled comparisons for Q1 and Q3.

1. Fixed candidate, different acceptance score. analyze_evidence.py lets each score select its own
   candidate, so its differences mix better selection with better acceptance. Here the candidate is
   fixed per incident by one rule, and only the score that decides whether to apply it changes. Two
   fixed selections: the gate's own (smallest residual) and a source-free one (the default template
   for the alert). Every score is certified on the same half of the calibration instances (the
   learned score fits on the other half) and evaluated on the same test instances.

2. A tolerance tuned the way an engineer would. On a development set, take the loosest tolerance whose
   observed harm is at most the target, freeze it, and deploy. Same candidates, same evidence, same
   labels as the gate; the only difference is the confidence correction. Run on the independent
   cohorts of analyze_cohorts.py and scored on the same held-out population.
Writes results/controls_summary.json.
"""
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent))
from analyze_evidence import ACTIONS, DETECTORS, is_real, load, make_splits  # noqa: E402

from dataranger.repair.gate import GRID, hb_pvalue, select  # noqa: E402

DELTA = 0.05
TAUS = np.round(np.arange(1.0, -0.001, -0.01), 2)
key = lambda r: (r["size"], r["seed"])


# ---------------------------------------------------------------- 1. fixed candidate, different score
def fixed(r, rule):
    if rule == "residual":
        return select(r)
    return next((c for c in r["candidates"] if c["fix"] == r["default"]), None)  # the default template for the alert


def feats(r, c):
    dets = [d for d in DETECTORS if d != "reconcile_gap"]
    return [float(c["action"] == a) for a in ACTIONS] + [float(d in r["detectors"]) for d in dets] + \
        [float(c["changed"]), float(c["invariants"]), float(c["monitors_quiet"]), float(r["reload_ok"]), float(len(r["candidates"]))]


def certify(scores, wrong, alpha, grid, higher_is_better):
    """Fixed-sequence Learn-then-Test over `grid`; returns the loosest certified threshold or None."""
    n, best = len(scores), None
    for t in grid:
        applied = scores >= t if higher_is_better else scores <= t
        if hb_pvalue(float(np.mean(applied & wrong)), n, alpha) <= DELTA:
            best = t
        else:
            break
    return best


def run_fixed(cal, test, alpha, rule, seed):
    inst = sorted({key(r) for r in cal})
    rng = np.random.default_rng(seed)
    rng.shuffle(inst)
    learn_k = set(inst[: len(inst) // 2])
    part = lambda recs, pred: [(r, fixed(r, rule)) for r in recs if not r["table_level"] and pred(r)]
    learn, tune, tst = part(cal, lambda r: key(r) in learn_k), part(cal, lambda r: key(r) not in learn_k), part(test, lambda r: True)
    ok = lambda c: c is not None and c["tier"] == "auto"      # an escalate-tier or missing candidate is never applied
    lr = [(r, c) for r, c in learn if ok(c)]
    clf = HistGradientBoostingClassifier(max_iter=200, random_state=seed).fit([feats(r, c) for r, c in lr], [int(c["correct"]) for r, c in lr])

    def arrays(pairs):
        elig = np.array([ok(c) for r, c in pairs])
        wrong = np.array([ok(c) and not c["correct"] for r, c in pairs])
        right_real = np.array([ok(c) and c["correct"] and is_real(r) for r, c in pairs])
        sc = {"invariants": np.array([float(ok(c) and c["invariants"]) for r, c in pairs]),
              "monitors": np.array([float(ok(c) and c["invariants"] and c["monitors_quiet"]) for r, c in pairs]),
              "residual": np.array([c["residual"] if ok(c) else np.inf for r, c in pairs])}
        p = np.zeros(len(pairs))
        if elig.any():
            p[elig] = clf.predict_proba([feats(r, c) for (r, c), e in zip(pairs, elig) if e])[:, 1]
        sc["learned"] = np.where(elig, p, -1.0)
        return sc, wrong, right_real, np.array([is_real(r) for r, c in pairs])

    s_t, w_t, _, _ = arrays(tune)
    s_e, w_e, rr_e, real_e = arrays(tst)
    out = {}
    for name, grid, hib in (("invariants", [1.0], True), ("monitors", [1.0], True), ("learned", TAUS, True), ("residual", GRID, False)):
        t = certify(s_t[name], w_t, alpha, grid, hib)
        applied = np.zeros(len(tst), bool) if t is None else (s_e[name] >= t if hib else s_e[name] <= t)
        out[name] = {"certified": float(t is not None), "auto_rate": float(applied.mean()), "harm": float((applied & w_e).mean()),
                     "real_errors_fixed": float((applied & rr_e).sum() / max(real_e.sum(), 1))}
    out["_selection_correct"] = float(np.mean([ok(c) and c["correct"] for r, c in tst]))
    return out


# ---------------------------------------------------------------- 2. tolerance tuned on a development set
def tuned_tolerance():
    src = Path("results/cohort_records.jsonl")
    recs = [json.loads(l) for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt"))]
    by = defaultdict(list)
    for r in recs:
        if not r["table_level"]:
            c = select(r)
            auto = c is not None and c["tier"] == "auto"
            by[key(r)].append((c["residual"] if auto else np.inf, bool(auto and not c["correct"])))
    keys = sorted(by)
    cal_keys, test_keys = keys[:2000], keys[2000:]
    arr = lambda ks: (np.array([x[0] for k in ks for x in by[k]]), np.array([x[1] for k in ks for x in by[k]]))
    res_te, wrong_te = arr(test_keys)
    harm_at = lambda lam: float(((res_te <= lam) & wrong_te).mean()) if lam is not None else 0.0
    auto_at = lambda lam: float((res_te <= lam).mean()) if lam is not None else 0.0
    out = {}
    for m in (100, 200, 400):
        cohorts = [cal_keys[i * m:(i + 1) * m] for i in range(2000 // m)]
        for alpha in (0.01, 0.02, 0.05):
            rows = []
            for ck in cohorts:
                res, wrong = arr(ck)
                emp = [float(((res <= lam) & wrong).mean()) for lam in GRID]
                ok = [lam for lam, e in zip(GRID, emp) if e <= alpha]       # risk is monotone in lam
                tuned = max(ok) if ok else None
                gate = certify(res, wrong, alpha, GRID, False)
                rows.append({"tuned": tuned, "tuned_harm": harm_at(tuned), "tuned_auto": auto_at(tuned),
                             "gate_harm": harm_at(gate), "gate_auto": auto_at(gate)})
            out[f"{m}|{alpha}"] = {"cohorts": len(rows),
                                   "tuned_exceed_share": float(np.mean([x["tuned_harm"] > alpha for x in rows])),
                                   "tuned_harm_mean": float(np.mean([x["tuned_harm"] for x in rows])), "tuned_harm_max": float(np.max([x["tuned_harm"] for x in rows])),
                                   "tuned_auto_mean": float(np.mean([x["tuned_auto"] for x in rows])),
                                   "gate_exceed_share": float(np.mean([x["gate_harm"] > alpha for x in rows])),
                                   "gate_harm_mean": float(np.mean([x["gate_harm"] for x in rows])), "gate_harm_max": float(np.max([x["gate_harm"] for x in rows])),
                                   "gate_auto_mean": float(np.mean([x["gate_auto"] for x in rows]))}
            v = out[f"{m}|{alpha}"]
            print(f"tuned tolerance m={m} alpha={alpha}: exceeds in {v['tuned_exceed_share']:.2f} of cohorts (harm mean {v['tuned_harm_mean']:.4f} max {v['tuned_harm_max']:.4f}, "
                  f"auto {v['tuned_auto_mean']:.3f}) | gate exceeds {v['gate_exceed_share']:.2f} (harm {v['gate_harm_mean']:.4f}, auto {v['gate_auto_mean']:.3f})", flush=True)
    return out


if __name__ == "__main__":
    out = {"tuned_tolerance": tuned_tolerance()}
    recs = load("results/repair_records.jsonl")
    splits = make_splits(recs, key)[:20]
    fx = {}
    for rule in ("residual", "default"):
        for alpha in (0.01, 0.02, 0.05):
            rows = [run_fixed(cal, test, alpha, rule, i) for i, (cal, test) in enumerate(splits)]
            for name in ("invariants", "monitors", "learned", "residual"):
                fx[f"{rule}|{name}|{alpha}"] = {k: float(np.mean([x[name][k] for x in rows])) for k in rows[0][name]}
                v = fx[f"{rule}|{name}|{alpha}"]
                print(f"fixed={rule:8s} score={name:10s} alpha={alpha}: certified {v['certified']:.2f} auto {v['auto_rate']:.3f} harm {v['harm']:.4f} fixed {v['real_errors_fixed']:.3f}", flush=True)
            fx[f"{rule}|selection_correct"] = float(np.mean([x["_selection_correct"] for x in rows]))
    out["fixed_candidate"] = fx
    json.dump(out, open("results/controls_summary.json", "w"), indent=1)
