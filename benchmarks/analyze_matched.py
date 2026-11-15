"""Matched comparison (reviewer request): the same candidates, the same selection (least residual among auto-tier
fixes), the same evidence and the same risk tiers, with and without the calibrated threshold, on the 1,000 held-out tables.
Writes results/matched_summary.json."""
import gzip, json
from collections import defaultdict
from pathlib import Path
import numpy as np
from analyze_cluster import ALPHAS, M, Scheme, certify, cluster_ids, key, outcome, mean_rows

src = Path("results/cohort_records.jsonl")
by = defaultdict(list)
for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt")):
    r = json.loads(l)
    if not r["table_level"]:
        by[key(r)].append(r)
keys = sorted(by)
cohorts = [[r for k in keys[i * M:(i + 1) * M] for r in by[k]] for i in range(2000 // M)]
test = [r for k in keys[2000:] for r in by[k]]
unit = cluster_ids(test)
res = Scheme("own", "residual")
rt = res.rows(test)
out = {"no_gate": outcome(rt, res.applied(rt, np.inf), unit)}
for a in ALPHAS:
    runs = []
    for c in cohorts:
        lam = certify(res, res.rows(c), a, cluster_ids(c))
        runs.append(outcome(rt, res.applied(rt, lam), unit))
    out[f"gate|{a}"] = mean_rows(runs)
json.dump(out, open("results/matched_summary.json", "w"), indent=1)
for k, v in out.items():
    print(f"{k:10s}", {x: round(100 * v[x], 2) for x in ("automated", "harm", "table_harm", "repair_wrong", "benign_wrong", "errors_fixed")})
