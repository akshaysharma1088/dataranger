"""Cluster-level certification as the primary protocol (Q1), on the independent population of run_cohorts.py.

The table (instance) is the unit: Theorem 4.2 needs tables to be independent draws and nothing about the
incidents inside one. The certified quantity is the expected within-table harm rate of a new table.
The 2,000 calibration tables are cut into five disjoint cohorts of 400; everything is evaluated on the same
1,000 held-out tables, with harm reported per incident and per table.

1. Table 1: outcome decomposition on the held-out tables for the uncalibrated baselines, reconciliation with a
   fixed tolerance, the cluster-level gate, the incident-level gate certified on the same cohorts, the
   cluster-level gate with the residual certified on two cohorts (800 tables), the grouped gate, and the gate on
   a learned evidence score.
2. Table 2: what the evidence adds. Each score with its own selection, and two fixed selections (smallest
   residual; the default template) with only the accepting score varied. Scores: declared invariants, the team's
   monitors, a learned score without the source, the same learned score with the source's residuals as features,
   and the raw residual. For cohort i the learned scores are fitted on 300 tables of cohort i+1, their threshold
   grid is the quantiles of their scores on that cohort's other 100 tables (so the grid is fixed before the
   certification data is seen), and every score is certified on cohort i with cluster losses.
3. Shift: thresholds and learned scores certified on the original mix, applied to the harder mix of run_hard.py.
Writes results/cluster_summary.json.
"""
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).parent))
from analyze_evidence import ACTIONS, DETECTORS, is_real, score_of, select_generic  # noqa: E402

from dataranger.repair.gate import GRID, decide, hb_pvalue, select  # noqa: E402

DELTA, M, ALPHAS = 0.05, 400, (0.01, 0.02, 0.05)
key = lambda r: (r["size"], r["seed"])
ok = lambda c: c is not None and c["tier"] == "auto"


def feats(r, c, src):
    dets = DETECTORS if src else [d for d in DETECTORS if d != "reconcile_gap"]
    x = [float(c["action"] == a) for a in ACTIONS] + [float(d in r["detectors"]) for d in dets] + \
        [float(c["changed"]), float(c["invariants"]), float(c["monitors_quiet"]), float(r["reload_ok"]), float(len(r["candidates"]))]
    return x + ([min(c["residual"], 10.0), min(c["residual_partition"], 10.0)] if src else [])


def default_of(r):
    return next((c for c in r["candidates"] if c["fix"] == r["default"]), None)


def cluster_ids(recs):
    ids = {}
    cid = np.array([ids.setdefault(key(r), len(ids)) for r in recs])
    return cid, len(ids), np.bincount(cid, minlength=len(ids)).astype(float)


class Scheme:
    """A selection rule plus an accepting score."""

    def __init__(self, selection, score):
        self.selection, self.score = selection, score
        self.learned = score.startswith("learned")
        self.src = score == "learned_src"
        self.grid = GRID if score.startswith("residual") else np.array([0.0])
        self.clf = None

    def pool(self, r):
        if self.selection == "own":
            return [c for c in r["candidates"] if c["tier"] == "auto"]
        c = select(r) if self.selection == "min_residual" else default_of(r)
        return [c] if ok(c) else []

    def prepare(self, fit_tables):
        """fit_tables: list of per-table record lists. Fit on the first 300, take the grid from the rest."""
        if not self.learned:
            return
        fit = [r for t in fit_tables[:300] for r in t]
        X = [feats(r, c, self.src) for r in fit for c in self.pool(r)]
        y = [int(c["correct"]) for r in fit for c in self.pool(r)]
        self.clf = HistGradientBoostingClassifier(max_iter=200, random_state=0).fit(X, y)
        held = self.rows([r for t in fit_tables[300:] for r in t])
        s = held["score"][held["elig"]]
        self.grid = np.unique(np.quantile(s, np.linspace(0, 1, 201)))[::-1]   # strictest (highest score) first

    def rows(self, recs):
        n = len(recs)
        chosen = [None] * n
        score = np.full(n, -1.0 if self.learned else np.inf)
        if self.learned:
            owner, X = [], []
            for i, r in enumerate(recs):
                for c in self.pool(r):
                    owner.append((i, c)); X.append(feats(r, c, self.src))
            p = self.clf.predict_proba(X)[:, 1] if X else []
            for (i, c), pi in zip(owner, p):
                if pi > score[i]:
                    score[i], chosen[i] = pi, c
        else:
            for i, r in enumerate(recs):
                if self.selection == "own":
                    c = select(r, self.score) if self.score.startswith("residual") else select_generic(r, self.score)
                else:
                    c = (self.pool(r) or [None])[0]
                chosen[i] = c
                if ok(c):
                    score[i] = c[self.score] if self.score.startswith("residual") else score_of(c, self.score)
        return {"score": score, "elig": np.array([ok(c) for c in chosen]),
                "wrong": np.array([ok(c) and not c["correct"] for c in chosen]),
                "none": np.array([c is not None and c["action"] == "none" for c in chosen]),
                "real": np.array([is_real(r) for r in recs])}

    def applied(self, rows, t):
        if t is None:
            return np.zeros(len(rows["score"]), bool)
        return rows["elig"] & ((rows["score"] >= t) if self.learned else (rows["score"] <= t))


def certify(scheme, rows, alpha, unit, mask=None, delta=DELTA):
    """Fixed-sequence Learn-then-Test. unit = (cid, n, size) for cluster losses, or None for incident losses.
    With a mask, the loss counts only masked incidents but is still normalised by all incidents of the unit."""
    best = None
    for t in scheme.grid:
        bad = scheme.applied(rows, t) & rows["wrong"]
        if mask is not None:
            bad = bad & mask
        if unit is None:
            risk, n = float(bad.mean()), len(bad)
        else:
            cid, n, size = unit
            risk = float((np.bincount(cid, weights=bad.astype(float), minlength=n) / size).mean())
        if hb_pvalue(risk, n, alpha) <= delta:
            best = t
        else:
            break
    return best


def outcome(rows, ap, unit):
    """Shares of all incidents by outcome, plus harm per incident and per table."""
    cid, n, size = unit
    w, real, none = rows["wrong"], rows["real"], rows["none"]
    right = ap & ~w
    return {"automated": float(ap.mean()), "repair_right": float((right & real).mean()), "repair_wrong": float((ap & w & ~none).mean()),
            "benign_right": float((right & ~real).mean()), "benign_wrong": float((ap & w & none).mean()), "escalated": float((~ap).mean()),
            "harm": float((ap & w).mean()), "table_harm": float((np.bincount(cid, weights=(ap & w).astype(float), minlength=n) / size).mean()),
            "errors_fixed": float((right & real).sum() / max(real.sum(), 1))}


def mean_rows(rows):
    v = {k: float(np.mean([x[k] for x in rows])) for k in rows[0]}
    v["harm_max"] = float(max(x["harm"] for x in rows)); v["table_harm_max"] = float(max(x["table_harm"] for x in rows))
    return v


def policy_rows(recs, policy):
    """Uncalibrated baselines of gate.decide, in the same row format (they ignore the risk tier)."""
    ch = [decide(r, policy) for r in recs]
    return {"elig": np.array([c is not None for c in ch]), "wrong": np.array([c is not None and not c["correct"] for c in ch]),
            "none": np.array([c is not None and c["action"] == "none" for c in ch]), "real": np.array([is_real(r) for r in recs]),
            "score": np.zeros(len(recs))}


if __name__ == "__main__":  # noqa
    src = Path("results/cohort_records.jsonl")
    by = defaultdict(list)
    for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt")):
        r = json.loads(l)
        if not r["table_level"]:
            by[key(r)].append(r)
    keys = sorted(by)
    tables = [[by[k] for k in keys[i * M:(i + 1) * M]] for i in range(2000 // M)]          # cohorts as lists of tables
    cohorts = [[r for t in c for r in t] for c in tables]
    test = [r for k in keys[2000:] for r in by[k]]
    t_unit = cluster_ids(test)
    sizes = np.array([len(by[k]) for k in keys])
    out = {"cohort_tables": M, "cohort_incidents_mean": float(np.mean([len(c) for c in cohorts])), "test_tables": t_unit[1], "test_incidents": len(test),
           "incidents_per_table": {"min": int(sizes.min()), "mean": float(sizes.mean()), "max": int(sizes.max())}}
    print(out, flush=True)
    hard_src = Path("results/hard_records.jsonl.gz")
    hard = [r for r in map(json.loads, gzip.open(hard_src, "rt")) if r["granularity"] == "channel" and not r["table_level"]] if hard_src.exists() else []
    h_unit = cluster_ids(hard) if hard else None

    # ---- 1. Table 1 ---------------------------------------------------------------------------------------------
    t1 = {}
    for pol in ("rules", "monitors"):
        pr = policy_rows(test, pol)
        t1[pol] = outcome(pr, pr["elig"], t_unit)
    res = Scheme("own", "residual")
    res_test = res.rows(test)
    res_cal = [res.rows(c) for c in cohorts]
    res_units = [cluster_ids(c) for c in cohorts]
    for name, lam in (("reconcile_exact", 0.0), ("reconcile_0.01", 0.01), ("reconcile_0.02", 0.02)):
        t1[name] = outcome(res_test, res.applied(res_test, lam), t_unit)
    shift = defaultdict(list)
    res_hard = res.rows(hard) if hard else None
    for alpha in ALPHAS:
        rows_c, rows_i, rows_2, rows_g, lam_c = [], [], [], [], []
        for i in range(len(cohorts)):
            lc = certify(res, res_cal[i], alpha, res_units[i])
            lam_c.append(-1.0 if lc is None else float(lc))
            rows_c.append(outcome(res_test, res.applied(res_test, lc), t_unit) | {"certified": float(lc is not None)})
            li = certify(res, res_cal[i], alpha, None)
            rows_i.append(outcome(res_test, res.applied(res_test, li), t_unit) | {"certified": float(li is not None)})
            j = (i + 1) % len(cohorts)                                   # two cohorts, 800 tables
            both = cohorts[i] + cohorts[j]
            l2 = certify(res, res.rows(both), alpha, cluster_ids(both))
            rows_2.append(outcome(res_test, res.applied(res_test, l2), t_unit) | {"certified": float(l2 is not None)})
            # grouped gate: one threshold for 'none' decisions, one for repairs; the risk budget and delta are split in two,
            # and each group's loss is its harmed incidents over all incidents of the table, so the two bounds add up
            lg = {g: certify(res, res_cal[i], alpha / 2, res_units[i], mask=(res_cal[i]["none"] == g), delta=DELTA / 2) for g in (True, False)}
            ap = (res_test["none"] & res.applied(res_test, lg[True])) | (~res_test["none"] & res.applied(res_test, lg[False]))
            rows_g.append(outcome(res_test, ap, t_unit) | {"certified": float(any(v is not None for v in lg.values()))})
            if hard:
                shift[f"residual|{alpha}"].append(outcome(res_hard, res.applied(res_hard, lc), h_unit))
        t1[f"gate_cluster|{alpha}"] = mean_rows(rows_c) | {"lambda_median": float(np.median(lam_c))}
        t1[f"gate_incident|{alpha}"] = mean_rows(rows_i)
        t1[f"gate_cluster_800|{alpha}"] = mean_rows(rows_2)
        t1[f"gate_cluster_grouped|{alpha}"] = mean_rows(rows_g)
    for k, v in t1.items():
        print(f"T1 {k:26s} auto {v['automated']:.3f} rep {v['repair_right']:.3f}/{v['repair_wrong']:.4f} ben {v['benign_right']:.3f}/{v['benign_wrong']:.4f} "
              f"harm {v['harm']:.4f} table {v['table_harm']:.4f} fixed {v['errors_fixed']:.3f}" + (f" cert {v['certified']:.1f} max {v['table_harm_max']:.4f}" if "certified" in v else ""), flush=True)
    out["table1"] = t1

    # ---- 2. Table 2 ---------------------------------------------------------------------------------------------
    settings = [("own", s) for s in ("invariants", "monitors", "learned_nosrc", "learned_src", "residual_partition", "residual")] + \
               [("min_residual", s) for s in ("invariants", "monitors", "learned_nosrc", "learned_src", "residual")] + \
               [("default", s) for s in ("learned_nosrc", "learned_src", "residual")]
    t2 = {}
    for sel, sc in settings:
        per_alpha = defaultdict(list)
        s = Scheme(sel, sc)
        fixed_test = None if s.learned else s.rows(test)
        for i, cal in enumerate(cohorts):
            s.prepare(tables[(i + 1) % len(cohorts)])
            cal_rows, unit = s.rows(cal), cluster_ids(cal)
            te = s.rows(test) if s.learned else fixed_test
            ha = s.rows(hard) if (hard and s.learned and sel == "own") else None
            for alpha in ALPHAS:
                t = certify(s, cal_rows, alpha, unit)
                o = outcome(te, s.applied(te, t), t_unit) | {"certified": float(t is not None)}
                per_alpha[alpha].append(o)
                if ha is not None:
                    shift[f"{sc}|{alpha}"].append(outcome(ha, s.applied(ha, t), h_unit))
        t2[f"{sel}|selection_correct"] = float(np.mean(~te["wrong"] & te["elig"]))
        for alpha in ALPHAS:
            v = mean_rows(per_alpha[alpha])
            t2[f"{sel}|{sc}|{alpha}"] = v
            print(f"T2 {sel:12s} {sc:18s} alpha={alpha}: certified {v['certified']:.1f} auto {v['automated']:.3f} harm {v['harm']:.4f} table-harm {v['table_harm']:.4f} "
                  f"(max {v['table_harm_max']:.4f}) fixed {v['errors_fixed']:.3f}", flush=True)
    out["table2"] = t2

    # ---- 3. shift to the harder mix ------------------------------------------------------------------------------
    out["shift_hard"] = {k: mean_rows(v) for k, v in shift.items()}
    for k, v in out["shift_hard"].items():
        print(f"SHIFT {k:22s} auto {v['automated']:.3f} harm {v['harm']:.4f} table-harm {v['table_harm']:.4f}", flush=True)
    json.dump(out, open("results/cluster_summary.json", "w"), indent=1)
