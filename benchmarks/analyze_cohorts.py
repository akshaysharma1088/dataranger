"""Independent calibration cohorts against one large held-out population (Q1).

The 100 overlapping splits of analyze_repair.py reuse the same 250 instances, so they are not 100
independent trials of the certification procedure, and the harm they measure on a 125-instance test
half carries its own sampling noise. This script uses the independent population from run_cohorts.py:

  * calibration pool: the first 2,000 instances, cut into DISJOINT cohorts of 100, 200 and 400
    instances (20, 10 and 5 cohorts), each an independent run of the procedure;
  * test population: the remaining 1,000 instances (about 26,000 incidents), the same for every cohort,
    large enough that its harm estimate is close to the population risk R.

For each cohort and target we certify a threshold with the incident-level gate (Theorem 4.1) and with
the cluster-level gate (Theorem 4.2, the instance as the cluster), then measure harm on the test
population per incident and per instance, with a cluster bootstrap over test instances for the
uncertainty. Writes results/cohort_summary.json.
"""
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from dataranger.repair.gate import calibrate, calibrate_clusters, decide, evaluate, loss

DELTA = 0.05
N_CAL, SIZES = 2000, (100, 200, 400)
import gzip
src = Path("results/cohort_records.jsonl")
recs = [json.loads(l) for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt"))]
key = lambda r: (r["size"], r["seed"])
by_inst = defaultdict(list)
for r in recs:
    by_inst[key(r)].append(r)
keys = sorted(by_inst)
assert len(keys) >= N_CAL + 500, len(keys)
cal_keys, test_keys = keys[:N_CAL], keys[N_CAL:]
test = [r for k in test_keys for r in by_inst[k]]
test_inc = [r for r in test if not r["table_level"]]
print(f"{len(keys)} instances: {N_CAL} calibration, {len(test_keys)} test ({len(test_inc)} test incidents)")


def inst_losses(lam):
    """Per-instance harm rates on the test population at threshold lam."""
    g = defaultdict(list)
    for r in test_inc:
        g[key(r)].append(loss(r, decide(r, "verified", lam)))
    return np.array([np.mean(v) for v in g.values()]), np.array([len(v) for v in g.values()])


def icc(groups):
    groups = [g for g in groups if len(g)]
    k, N = len(groups), sum(len(g) for g in groups)
    grand = np.mean([x for g in groups for x in g])
    msb = sum(len(g) * (np.mean(g) - grand) ** 2 for g in groups) / (k - 1)
    msw = sum(((np.array(g) - np.mean(g)) ** 2).sum() for g in groups) / (N - k)
    n0 = (N - sum(len(g) ** 2 for g in groups) / N) / (k - 1)
    return float((msb - msw) / (msb + (n0 - 1) * msw)) if msb + (n0 - 1) * msw > 0 else 0.0


rng = np.random.default_rng(0)
B = 1000
out = {"instances": len(keys), "calibration_instances": N_CAL, "test_instances": len(test_keys), "test_incidents": len(test_inc)}
res = {}
for m in SIZES:
    cohorts = [cal_keys[i * m:(i + 1) * m] for i in range(N_CAL // m)]
    for level in ("incident", "cluster"):
        for alpha in (0.01, 0.02, 0.05):
            rows = []
            for ck in cohorts:
                cal = [r for k in ck for r in by_inst[k]]
                lam = calibrate(cal, alpha, DELTA) if level == "incident" else calibrate_clusters(cal, key, alpha, DELTA)
                e = evaluate(test, "verified", lam)
                il, sizes = inst_losses(lam)
                # cluster bootstrap over test instances: incident-weighted harm and per-instance mean
                idx = rng.integers(0, len(il), (B, len(il)))
                boot_inc = (il[idx] * sizes[idx]).sum(1) / sizes[idx].sum(1)
                boot_ins = il[idx].mean(1)
                rows.append({"lam": lam, "certified": lam >= 0, "auto": e["auto_rate"], "harm": e["harm"], "fixed": e["real_errors_fixed"],
                             "inst_harm": float(il.mean()), "harm_ci": [float(np.quantile(boot_inc, 0.025)), float(np.quantile(boot_inc, 0.975))],
                             "inst_harm_ci": [float(np.quantile(boot_ins, 0.025)), float(np.quantile(boot_ins, 0.975))],
                             "exceeds": e["harm"] > alpha, "inst_exceeds": float(il.mean()) > alpha,
                             "ci_above": float(np.quantile(boot_inc, 0.025)) > alpha})
            res[f"{level}|{m}|{alpha}"] = {
                "cohorts": len(rows), "cohort_instances": m, "cohort_incidents_mean": float(np.mean([sum(not r["table_level"] for k in ck for r in by_inst[k]) for ck in cohorts])),
                "certified_share": float(np.mean([x["certified"] for x in rows])),
                "lambda_median": float(np.median([x["lam"] for x in rows])),
                "auto_mean": float(np.mean([x["auto"] for x in rows])), "auto_min": float(np.min([x["auto"] for x in rows])), "auto_max": float(np.max([x["auto"] for x in rows])),
                "harm_mean": float(np.mean([x["harm"] for x in rows])), "harm_max": float(np.max([x["harm"] for x in rows])),
                "inst_harm_mean": float(np.mean([x["inst_harm"] for x in rows])), "inst_harm_max": float(np.max([x["inst_harm"] for x in rows])),
                "fixed_mean": float(np.mean([x["fixed"] for x in rows])),
                "exceed_share": float(np.mean([x["exceeds"] for x in rows])), "inst_exceed_share": float(np.mean([x["inst_exceeds"] for x in rows])),
                "ci_above_share": float(np.mean([x["ci_above"] for x in rows])),
                "harm_ci_halfwidth_mean": float(np.mean([(x["harm_ci"][1] - x["harm_ci"][0]) / 2 for x in rows])),
                "per_cohort": rows}
            v = res[f"{level}|{m}|{alpha}"]
            print(f"{level:8s} m={m:4d} alpha={alpha}: certified {v['certified_share']:.2f} lam {v['lambda_median']:.4f} auto {v['auto_mean']:.3f} "
                  f"[{v['auto_min']:.3f},{v['auto_max']:.3f}] harm {v['harm_mean']:.4f} (max {v['harm_max']:.4f}, ci +-{v['harm_ci_halfwidth_mean']:.4f}) "
                  f"inst {v['inst_harm_mean']:.4f} exceed {v['exceed_share']:.2f}/{v['inst_exceed_share']:.2f} ci_above {v['ci_above_share']:.2f}")
out["cohorts"] = res

# Dependence on the full population at the thresholds a 400-instance cohort certifies.
dep = {}
for alpha in (0.01, 0.02, 0.05):
    lam = res[f"incident|400|{alpha}"]["lambda_median"]
    g = defaultdict(list)
    for r in test_inc:
        g[key(r)].append(loss(r, decide(r, "verified", lam)))
    m = np.mean([len(v) for v in g.values()])
    i = icc(list(g.values()))
    dep[str(alpha)] = {"icc": i, "design_effect": float(1 + (m - 1) * max(i, 0.0)), "incidents_per_instance": float(m)}
    print(f"ICC at alpha={alpha}: {i:.4f}, design effect {dep[str(alpha)]['design_effect']:.2f}")
out["dependence_test_population"] = dep

# Baselines on the test population.
out["baselines_test"] = {p: evaluate(test, p) for p in ("rules", "monitors")}
out["baselines_test"].update({f"fixed_{t}": evaluate(test, "verified", t) for t in (0.0, 0.01, 0.02)})
json.dump(out, open("results/cohort_summary.json", "w"), indent=1)
