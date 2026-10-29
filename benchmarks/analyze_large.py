"""Large tables (about 480k rows): does a threshold calibrated on small and medium tables transfer?

Also reports the grouped gate (separate thresholds for 'none' decisions and repairs).
Writes results/large_summary.json.
"""
import gzip
import json
from collections import defaultdict

import numpy as np

from dataranger.repair.gate import calibrate, calibrate_groups, evaluate, evaluate_groups

small_medium = [json.loads(l) for l in gzip.open("results/repair_records.jsonl.gz", "rt")]
large = [json.loads(l) for l in gzip.open("results/repair_records_large.jsonl.gz", "rt")]
out = {"large_incidents": sum(not r["table_level"] for r in large),
       "baselines_large": {p: evaluate(large, p) for p in ("rules", "monitors")}}
for alpha in (0.01, 0.02, 0.05):
    lam = calibrate(small_medium, alpha, 0.05)
    out[f"transfer|{alpha}"] = evaluate(large, "verified", lam) | {"lambda": lam}
    lams = calibrate_groups(small_medium, alpha, 0.05)
    out[f"transfer_grouped|{alpha}"] = evaluate_groups(large, lams) | {"lambdas": lams}
by = defaultdict(list)
for r in large:
    by[r["seed"]].append(r)
keys = sorted(by)
rng = np.random.default_rng(0)
for alpha in (0.02, 0.05):
    vals = []
    for _ in range(50):
        p = rng.permutation(len(keys))
        cal = [r for i in p[:20] for r in by[keys[i]]]
        test = [r for i in p[20:] for r in by[keys[i]]]
        vals.append(evaluate(test, "verified", calibrate(cal, alpha, 0.05)))
    out[f"within_large|{alpha}"] = {k: float(np.mean([v[k] for v in vals])) for k in ("auto_rate", "harm", "real_errors_fixed")} | \
        {"violations": float(np.mean([v["harm"] > alpha for v in vals]))}
json.dump(out, open("results/large_summary.json", "w"), indent=1)
for k, v in out.items():
    print(k, {a: (round(b, 4) if isinstance(b, float) else b) for a, b in v.items()} if isinstance(v, dict) else v)
