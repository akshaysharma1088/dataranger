"""What corrupt control totals do to the gate and to deterministic reconciliation.

  in-distribution : calibrate and test on instances with the same corruption rate (100 splits)
  shift           : calibrate on clean totals (rate 0), test on corrupt ones
  reconciliation  : exact match and a 1% tolerance, no calibration
Writes results/corrupt_summary.json.
"""
import gzip
import json
from collections import defaultdict

import numpy as np

from dataranger.repair.gate import calibrate, evaluate

DELTA, SPLITS = 0.05, 100
clean = [json.loads(l) for l in gzip.open("results/repair_records.jsonl.gz", "rt")]
clean = [r for r in clean if r["size"] == "small"]
corrupt = [json.loads(l) for l in gzip.open("results/corrupt_records.jsonl.gz", "rt")]
data = {0.0: clean}
for r in corrupt:
    data.setdefault(r["p_corrupt"], []).append(r)


def splits(recs, seed=0):
    by = defaultdict(list)
    for r in recs:
        by[r["seed"]].append(r)
    keys = sorted(by)
    rng = np.random.default_rng(seed)
    for _ in range(SPLITS):
        perm = rng.permutation(len(keys))
        ck = {keys[i] for i in perm[: len(keys) // 2]}
        yield [r for k in keys if k in ck for r in by[k]], [r for k in keys if k not in ck for r in by[k]], ck


out = {}
for p, recs in sorted(data.items()):
    inc = [r for r in recs if not r["table_level"]]
    res = {"incidents": len(inc),
           "exact_match": evaluate(inc, "verified", 0.0), "tolerance_0.01": evaluate(inc, "verified", 0.01)}
    for alpha in (0.01, 0.05):
        ind, shift = [], []
        for (cal, test, ck) in splits(recs):
            lam = calibrate(cal, alpha, DELTA)
            e = evaluate(test, "verified", lam)
            ind.append({**e, "lam": lam})
            if p > 0:  # calibrate on the clean versions of the calibration seeds, test on the corrupt test seeds
                lam0 = calibrate([r for r in clean if r["seed"] in ck], alpha, DELTA)
                shift.append({**evaluate(test, "verified", lam0), "lam": lam0})
        summ = lambda rows: {"auto_rate": float(np.mean([x["auto_rate"] for x in rows])),
                             "harm": float(np.mean([x["harm"] for x in rows])),
                             "violation_rate": float(np.mean([x["harm"] > alpha for x in rows])),
                             "lambda_median": float(np.median([x["lam"] for x in rows]))}
        res[f"in_distribution|{alpha}"] = summ(ind)
        if shift:
            res[f"shift|{alpha}"] = summ(shift)
    out[str(p)] = res
    print(p, json.dumps(res))
json.dump(out, open("results/corrupt_summary.json", "w"), indent=1)
