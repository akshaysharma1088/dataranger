"""The gate on TPC-DS store_sales: in-domain calibration, transfer from the synthetic benchmark,
and hand-set tolerances. Splits are by 60-day window, so the three injections into one window
never sit on both sides of a split. Writes results/tpcds_summary.json.
"""
import gzip
import json
from collections import Counter, defaultdict

import numpy as np

from dataranger.repair.gate import calibrate, evaluate, select

DELTA, SPLITS = 0.05, 100
recs = [json.loads(l) for l in gzip.open("results/tpcds_records.jsonl.gz", "rt")]
inc = [r for r in recs if not r["table_level"]]
syn = [json.loads(l) for l in gzip.open("results/repair_records.jsonl.gz", "rt")]


def kind_of(r):
    real = [k for k in r["kinds"] if k != "false_alarm"]
    return "+".join(sorted(set(real))) if real else "false_alarm"


out = {"instances": len({(r["window"], r["rep"]) for r in recs}), "windows": len({r["window"] for r in recs}),
       "incidents": len(inc), "mix": dict(Counter(kind_of(r) for r in inc)),
       "seconds_per_instance_median": float(np.median([r["seconds"] for r in recs]))}
best = defaultdict(lambda: [0, 0])
for r in inc:
    b = select(r)
    best[kind_of(r)][0] += 1
    best[kind_of(r)][1] += bool(b and b["correct"])
out["best_candidate_correct"] = {k: v[1] / v[0] for k, v in best.items()}
out["all"] = {p: evaluate(inc, p) for p in ("rules", "monitors")}

# Transfer: thresholds certified on the synthetic benchmark, applied unchanged to TPC-DS.
out["transfer"] = {}
for alpha in (0.01, 0.02, 0.05):
    lam = calibrate(syn, alpha, DELTA)
    out["transfer"][str(alpha)] = {"lambda": lam, **evaluate(inc, "verified", lam)}

by_w = defaultdict(list)
for r in recs:
    by_w[r["window"]].append(r)
ws = sorted(by_w)
rng = np.random.default_rng(0)
splits = []
for _ in range(SPLITS):
    perm = rng.permutation(len(ws))
    ck = {ws[i] for i in perm[: len(ws) // 2]}
    splits.append(([r for w in ws if w in ck for r in by_w[w]], [r for w in ws if w not in ck for r in by_w[w]]))

out["calibrated"] = {}
for alpha in (0.01, 0.02, 0.05):
    rows = []
    for cal, test in splits:
        lam = calibrate(cal, alpha, DELTA)
        rows.append({"lam": lam, "pop_harm": evaluate(inc, "verified", lam)["harm"], **evaluate(test, "verified", lam)})
    out["calibrated"][str(alpha)] = {"lambda_median": float(np.median([x["lam"] for x in rows])),
                                     "auto_rate_mean": float(np.mean([x["auto_rate"] for x in rows])),
                                     "harm_mean": float(np.mean([x["harm"] for x in rows])),
                                     "harm_max": float(np.max([x["harm"] for x in rows])),
                                     "real_errors_fixed_mean": float(np.mean([x["real_errors_fixed"] for x in rows])),
                                     "violation_rate": float(np.mean([x["harm"] > alpha for x in rows])),
                                     # harm of the certified threshold on all 4,299 incidents, a less noisy proxy
                                     # for its true risk than one held-out half of 15 windows
                                     "population_violation_rate": float(np.mean([x["pop_harm"] > alpha for x in rows]))}

out["fixed_threshold"] = {}
for lam in (0.005, 0.01, 0.02, 0.05):
    hs = [evaluate(test, "verified", lam)["harm"] for _, test in splits]
    out["fixed_threshold"][str(lam)] = {"harm_mean": float(np.mean(hs)), "auto_rate": evaluate(inc, "verified", lam)["auto_rate"],
                                        "violates_0.01": float(np.mean([h > 0.01 for h in hs])),
                                        "violates_0.05": float(np.mean([h > 0.05 for h in hs]))}

json.dump(out, open("results/tpcds_summary.json", "w"), indent=1)
print(json.dumps(out, indent=1))
