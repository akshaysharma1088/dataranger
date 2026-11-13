"""Cluster-level follow-ups to analyze_cluster.py. Writes results/cluster_extra.json.

1. On the held-out tables of Q1, at the thresholds the cluster-level gate certifies: why incidents were escalated,
   and which incident types the remaining harm falls on.
2. Partition-level totals only (Q3): a fixed 1% tolerance against the cluster-level gate recalibrated on that evidence.
3. The harder mix, in domain (Q8): 500 independent tables per evidence granularity (run_hard_cohort.py), five folds of
   100 test tables with the other 400 calibrating; the single gate, the grouped gate with a split budget, fixed
   tolerances, and how each hard family fared. Folds share calibration tables, so their mean is descriptive.
4. Shift (Q8): thresholds certified on the original mix, by family on the harder mix of run_hard.py.
"""
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analyze_cluster import ALPHAS, DELTA, M, Scheme, certify, cluster_ids, key, mean_rows, ok, outcome  # noqa: E402
from analyze_evidence import is_real  # noqa: E402

from dataranger.repair.gate import select  # noqa: E402

HARD_KINDS = ("compound", "multi_version", "category_relabel", "missing_manifest")


def load_tables(path, pred=lambda r: True):
    by = defaultdict(list)
    src = Path(path)
    for l in (open(src) if src.suffix == ".jsonl" and src.exists() else gzip.open(src if src.suffix == ".gz" else f"{src}.gz", "rt")):
        r = json.loads(l)
        if not r["table_level"] and pred(r):
            by[key(r)].append(r)
    return by


def families(recs, rows, ap, kinds):
    out = {}
    for k in kinds:
        m = np.array([k in r["kinds"] for r in recs])
        if m.sum():
            right = ap & ~rows["wrong"]
            out[k] = {"n": int(m.sum()), "escalated": float((~ap & m).sum() / m.sum()), "repaired": float((right & m).sum() / m.sum()),
                      "harm": float((ap & rows["wrong"] & m).sum() / m.sum())}
    return out


def grouped(res, cal_rows, unit, test_rows, alpha):
    lg = {g: certify(res, cal_rows, alpha / 2, unit, mask=(cal_rows["none"] == g), delta=DELTA / 2) for g in (True, False)}
    ap = (test_rows["none"] & res.applied(test_rows, lg[True])) | (~test_rows["none"] & res.applied(test_rows, lg[False]))
    return ap, lg


if __name__ == "__main__":
    out = {}
    res = Scheme("own", "residual")
    by = load_tables("results/cohort_records.jsonl")
    keys = sorted(by)
    cohorts = [[r for k in keys[i * M:(i + 1) * M] for r in by[k]] for i in range(2000 // M)]
    test = [r for k in keys[2000:] for r in by[k]]
    t_unit, te = cluster_ids(test), res.rows(test)
    cal_rows = [res.rows(c) for c in cohorts]
    units = [cluster_ids(c) for c in cohorts]

    # ---- 1. escalations and remaining harm on the held-out tables -------------------------------------------------
    for alpha in ALPHAS:
        esc, harm_kinds, harm_actions = Counter(), Counter(), Counter()
        for i in range(len(cohorts)):
            lam = certify(res, cal_rows[i], alpha, units[i])
            ap = res.applied(te, lam)
            for r, a, w in zip(test, ap, te["wrong"]):
                c = select(r)
                if not a:
                    has_right = any(x["correct"] and x["tier"] == "auto" for x in r["candidates"])
                    esc["no_correct_candidate" if not has_right else ("correct_selected_gap_above_threshold" if ok(c) and c["correct"] else "correct_not_selected")] += 1
                elif w:
                    harm_actions["benign" if c["action"] == "none" else "repair"] += 1
                    for k in r["kinds"]:
                        harm_kinds[k] += 1
        tot, th = sum(esc.values()), max(sum(harm_actions.values()), 1)
        out[f"escalations|{alpha}"] = {k: v / tot for k, v in esc.items()}
        out[f"harm_kinds|{alpha}"] = {k: v / th for k, v in harm_kinds.most_common(6)} | {"harmed_incidents_per_cohort": th / len(cohorts)}
        out[f"harm_actions|{alpha}"] = {k: v / th for k, v in harm_actions.items()}
        print(alpha, "escalations", {k: round(v, 3) for k, v in out[f"escalations|{alpha}"].items()}, "| harm", {k: round(v, 3) for k, v in out[f"harm_kinds|{alpha}"].items()},
              {k: round(v, 3) for k, v in out[f"harm_actions|{alpha}"].items()}, flush=True)

    # ---- 2. partition totals only --------------------------------------------------------------------------------
    part = Scheme("own", "residual_partition")
    pt = part.rows(test)
    out["partition|tolerance_0.01"] = outcome(pt, part.applied(pt, 0.01), t_unit)
    for alpha in ALPHAS:
        rows, lams = [], []
        for c in cohorts:
            lam = certify(part, part.rows(c), alpha, cluster_ids(c))
            lams.append(-1.0 if lam is None else float(lam))
            rows.append(outcome(pt, part.applied(pt, lam), t_unit))
        out[f"partition|gate_cluster|{alpha}"] = mean_rows(rows) | {"lambda_median": float(np.median(lams))}
    print("partition totals: 1% tolerance", {k: round(v, 4) for k, v in out["partition|tolerance_0.01"].items() if k in ("automated", "harm", "table_harm")},
          "| gate 1%", {k: round(v, 4) for k, v in out["partition|gate_cluster|0.01"].items() if k in ("automated", "harm", "table_harm", "lambda_median")}, flush=True)

    # ---- 3. the harder mix, in domain ---------------------------------------------------------------------------
    hard_path = Path("results/hard_cohort_records.jsonl.gz")
    if hard_path.exists():
        for gran in ("channel", "channel+category"):
            hb = load_tables(hard_path, lambda r: r["granularity"] == gran)
            hk = sorted(hb)
            assert len(hk) == 500, len(hk)
            folds = [hk[i * 100:(i + 1) * 100] for i in range(5)]
            agg = defaultdict(list)
            fam = defaultdict(lambda: defaultdict(list))
            for f in range(5):
                tst = [r for k in folds[f] for r in hb[k]]
                cal = [r for g in range(5) if g != f for k in folds[g] for r in hb[k]]
                cr, cu, tr, tu = res.rows(cal), cluster_ids(cal), res.rows(tst), cluster_ids(tst)
                for name, lam in (("exact", 0.0), ("tolerance_0.01", 0.01)):
                    agg[name].append(outcome(tr, res.applied(tr, lam), tu))
                for alpha in ALPHAS:
                    lam = certify(res, cr, alpha, cu)
                    ap = res.applied(tr, lam)
                    agg[f"gate_cluster|{alpha}"].append(outcome(tr, ap, tu) | {"certified": float(lam is not None)})
                    for k, v in families(tst, tr, ap, HARD_KINDS).items():
                        for m, x in v.items():
                            fam[f"gate_cluster|{alpha}|{k}"][m].append(x)
                    apg, lg = grouped(res, cr, cu, tr, alpha)
                    agg[f"gate_cluster_grouped|{alpha}"].append(outcome(tr, apg, tu) | {"certified": float(any(v is not None for v in lg.values()))})
                    li = certify(res, cr, alpha, None)
                    agg[f"gate_incident|{alpha}"].append(outcome(tr, res.applied(tr, li), tu) | {"certified": float(li is not None)})
            exceed = {k: float(np.mean([x["table_harm" if "cluster" in k else "harm"] > float(k.split("|")[1]) for x in v]))
                      for k, v in agg.items() if "|" in k}
            out[f"hard_exceeds|{gran}"] = exceed
            out[f"hard|{gran}"] = {k: mean_rows(v) for k, v in agg.items()} | {"tables": len(hk), "incidents": sum(len(hb[k]) for k in hk),
                                                                                "test_incidents_per_fold": float(np.mean([sum(len(hb[k]) for k in f) for f in folds]))}
            out[f"hard_families|{gran}"] = {k: {m: float(np.mean(x)) for m, x in v.items()} for k, v in fam.items()}
            print(f"\nharder mix, {gran}: {len(hk)} tables, {out[f'hard|{gran}']['incidents']} incidents")
            for k, v in out[f"hard|{gran}"].items():
                if isinstance(v, dict):
                    print(f"  {k:28s} auto {v['automated']:.3f} harm {v['harm']:.4f} table-harm {v['table_harm']:.4f} (max {v['table_harm_max']:.4f}) fixed {v['errors_fixed']:.3f}"
                          + (f" certified {v['certified']:.1f}" if "certified" in v else ""), flush=True)
            for k, v in out[f"hard_families|{gran}"].items():
                if k.startswith("gate_cluster|0.01") or k.startswith("gate_cluster|0.05"):
                    print(f"  family {k:44s}", {m: round(x, 3) for m, x in v.items()})

    # ---- 4. shift: original-mix thresholds on the harder mix, by family ----------------------------------------
    hs = Path("results/hard_cohort_records.jsonl.gz")
    if hs.exists():
        hard = [r for k, t in sorted(load_tables(hs, lambda r: r["granularity"] == "channel").items()) for r in t]
        hr, hu = res.rows(hard), cluster_ids(hard)
        for alpha in ALPHAS:
            rows, fam = [], defaultdict(lambda: defaultdict(list))
            for i in range(len(cohorts)):
                lam = certify(res, cal_rows[i], alpha, units[i])
                ap = res.applied(hr, lam)
                rows.append(outcome(hr, ap, hu))
                for k, v in families(hard, hr, ap, HARD_KINDS).items():
                    for m, x in v.items():
                        fam[k][m].append(x)
            out[f"shift|{alpha}"] = mean_rows(rows) | {"families": {k: {m: float(np.mean(x)) for m, x in v.items()} for k, v in fam.items()}, "incidents": len(hard)}
        print("\nshift at 1%:", {k: round(v, 4) for k, v in out["shift|0.01"].items() if k in ("automated", "harm", "table_harm")},
              {k: {m: round(x, 3) for m, x in v.items()} for k, v in out["shift|0.01"]["families"].items()})
    json.dump(out, open("results/cluster_extra.json", "w"), indent=1)
