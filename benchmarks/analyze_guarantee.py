"""Does the guarantee survive dependence between incidents of the same table?

Incidents from one benchmark instance share a table, a month and a manifest, so they are not
independent. This script measures how much that matters, with the same 100 instance-level splits
as analyze_repair.py:

  1. Intra-instance correlation of the loss at the incident-level threshold, and the design effect
     it implies for the effective calibration size.
  2. Cluster-level Learn-then-Test (the instance is the unit), which needs no independence inside
     an instance. Harm is reported both per incident and as the mean per-instance harm rate, which
     is the quantity the cluster-level bound controls.
Writes results/guarantee_summary.json.
"""
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from dataranger.repair.gate import calibrate, calibrate_clusters, decide, evaluate, loss

src = Path("results/repair_records.jsonl")
recs = [json.loads(l) for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt"))]
key = lambda r: (r["size"], r["seed"])
by_inst = defaultdict(list)
for r in recs:
    by_inst[key(r)].append(r)
keys = sorted(by_inst)
rng = np.random.default_rng(0)  # same splits as analyze_repair.py
SPLITS, DELTA = 100, 0.05
splits = []
for _ in range(SPLITS):
    perm = rng.permutation(len(keys))
    cal_k = {keys[i] for i in perm[: len(keys) // 2]}
    splits.append(([r for k in keys if k in cal_k for r in by_inst[k]],
                   [r for k in keys if k not in cal_k for r in by_inst[k]]))


def icc(groups: list[list[int]]) -> float:
    """One-way ANOVA intraclass correlation for unequal group sizes."""
    groups = [g for g in groups if len(g)]
    k, N = len(groups), sum(len(g) for g in groups)
    grand = np.mean([x for g in groups for x in g])
    msb = sum(len(g) * (np.mean(g) - grand) ** 2 for g in groups) / (k - 1)
    msw = sum(((np.array(g) - np.mean(g)) ** 2).sum() for g in groups) / (N - k)
    n0 = (N - sum(len(g) ** 2 for g in groups) / N) / (k - 1)
    return float((msb - msw) / (msb + (n0 - 1) * msw)) if msb + (n0 - 1) * msw > 0 else 0.0


def inst_harm(test, lam, cluster_level=True):
    g = defaultdict(list)
    for r in test:
        if not r["table_level"]:
            g[key(r)].append(loss(r, decide(r, "verified", lam)))
    return float(np.mean([np.mean(v) for v in g.values()]))


out = {"instances": len(keys), "incidents_per_instance_mean": float(np.mean([sum(not r["table_level"] for r in v) for v in by_inst.values()]))}
dep = {}
for alpha in (0.01, 0.02, 0.05):
    iccs = []
    for cal, test in splits[:30]:
        lam = calibrate(cal, alpha, DELTA)
        g = defaultdict(list)
        for r in test:
            if not r["table_level"]:
                g[key(r)].append(loss(r, decide(r, "verified", lam)))
        iccs.append(icc(list(g.values())))
    m = out["incidents_per_instance_mean"]
    dep[str(alpha)] = {"icc_mean": float(np.mean(iccs)), "icc_max": float(np.max(iccs)),
                       "design_effect": float(1 + (m - 1) * max(np.mean(iccs), 0.0))}
out["dependence"] = dep

clu = {}
for alpha in (0.01, 0.02, 0.05, 0.1):
    rows = []
    for cal, test in splits:
        lam = calibrate_clusters(cal, key, alpha, DELTA)
        e = evaluate(test, "verified", lam)
        ih = inst_harm(test, lam)
        rows.append({"lam": lam, "auto": e["auto_rate"], "harm": e["harm"], "inst_harm": ih})
    clu[str(alpha)] = {"lambda_median": float(np.median([x["lam"] for x in rows])),
                       "certified_share": float(np.mean([x["lam"] >= 0 for x in rows])),
                       "auto_rate_mean": float(np.mean([x["auto"] for x in rows])),
                       "harm_mean": float(np.mean([x["harm"] for x in rows])),
                       "instance_harm_mean": float(np.mean([x["inst_harm"] for x in rows])),
                       "violation_rate": float(np.mean([x["inst_harm"] > alpha for x in rows]))}
out["cluster_ltt"] = clu

# Incident-level LTT, judged by the per-instance harm rate too, for comparison.
inc = {}
for alpha in (0.01, 0.02, 0.05):
    ih = [inst_harm(test, calibrate(cal, alpha, DELTA)) for cal, test in splits]
    inc[str(alpha)] = {"instance_harm_mean": float(np.mean(ih)), "violation_rate_instance": float(np.mean([h > alpha for h in ih]))}
out["incident_ltt_instance_view"] = inc

json.dump(out, open("results/guarantee_summary.json", "w"), indent=1)
print(json.dumps(out, indent=1))
