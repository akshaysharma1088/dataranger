"""The gate on the harder error families (Section 6.8).

  in-domain   calibrate and test on the hard mix (100 instance-level splits), per-channel totals
  shift       calibrate on the main benchmark's small instances (same seeds, no hard types), test on the hard mix
  finer       in-domain again, with the source also publishing per-channel-and-category totals
Per type: how the gate handled each hard family (repaired, declared benign, escalated; harm). Writes
results/hard_summary.json.
"""
import gzip
import json
from collections import defaultdict

import numpy as np

from dataranger.repair.gate import calibrate, calibrate_groups, decide, evaluate_groups, loss

DELTA, SPLITS = 0.05, 100
NOT_REAL = {"false_alarm", "volume_spike", "big_ticket_day", "missing_manifest"}
HARD = ("compound", "multi_version", "category_relabel", "missing_manifest")
recs = [json.loads(l) for l in gzip.open("results/hard_records.jsonl.gz", "rt")]
main = [json.loads(l) for l in gzip.open("results/repair_records.jsonl.gz", "rt")]
main = [r for r in main if r["size"] == "small"]
data = defaultdict(list)
for r in recs:
    data[r["granularity"]].append(r)


def splits(seeds, n=SPLITS, seed=0):
    rng = np.random.default_rng(seed)
    for _ in range(n):
        perm = rng.permutation(len(seeds))
        yield {seeds[i] for i in perm[: len(seeds) // 2]}


def evaluate(recs, lam):
    inc = [r for r in recs if not r["table_level"]]
    applied = [decide(r, "verified", lam) for r in inc]
    harm = [loss(r, a) for r, a in zip(inc, applied)]
    real = [any(k not in NOT_REAL for k in r["kinds"]) for r in inc]
    return {"incidents": len(inc), "auto_rate": float(np.mean([a is not None for a in applied])), "harm": float(np.mean(harm)),
            "real_errors_fixed": float(sum(a is not None and a["correct"] and a["action"] != "none" and x for a, x in zip(applied, real)) / max(sum(real), 1))}


def per_type(recs, lam):
    out = {}
    for k in HARD:
        rs = [r for r in recs if not r["table_level"] and k in r["kinds"]]
        if not rs:
            continue
        c = defaultdict(int)
        for r in rs:
            a = decide(r, "verified", lam)
            if a is None:
                c["escalated"] += 1
            elif a["action"] == "none":
                c["benign_right" if a["correct"] else "benign_wrong"] += 1
            else:
                c["repair_right" if a["correct"] else "repair_wrong"] += 1
        n = len(rs)
        out[k] = {"n": n, **{x: c[x] / n for x in ("escalated", "benign_right", "benign_wrong", "repair_right", "repair_wrong")},
                  "harm": (c["benign_wrong"] + c["repair_wrong"]) / n,
                  "correct_available": float(np.mean([any(x["correct"] for x in r["candidates"]) for r in rs]))}
    return out


out = {}
for gran, rs in sorted(data.items()):
    seeds = sorted({r["seed"] for r in rs})
    by = defaultdict(list)
    for r in rs:
        by[r["seed"]].append(r)
    by_main = defaultdict(list)
    for r in main:
        by_main[r["seed"]].append(r)
    res = {"incidents": sum(not r["table_level"] for r in rs),
           "mix": {k: sum(k in r["kinds"] for r in rs if not r["table_level"]) for k in HARD},
           "exact_match": evaluate(rs, 0.0), "tolerance_0.01": evaluate(rs, 0.01)}
    for alpha in (0.01, 0.02, 0.05):
        ind, grp, shift, types, types_shift = [], [], [], [], []
        for ck in splits(seeds):
            cal = [r for s in seeds if s in ck for r in by[s]]
            test = [r for s in seeds if s not in ck for r in by[s]]
            lam = calibrate(cal, alpha, DELTA)
            ind.append({**evaluate(test, lam), "lam": lam})
            types.append(per_type(test, lam))
            lams = calibrate_groups(cal, alpha, DELTA)
            grp.append(evaluate_groups(test, lams))
            if gran == "channel":
                lam0 = calibrate([r for s in seeds if s in ck for r in by_main[s]], alpha, DELTA)
                shift.append({**evaluate(test, lam0), "lam": lam0})
                types_shift.append(per_type(test, lam0))
        summ = lambda rows: {"auto_rate": float(np.mean([x["auto_rate"] for x in rows])), "harm": float(np.mean([x["harm"] for x in rows])),
                             "harm_max": float(np.max([x["harm"] for x in rows])), "exceed_share": float(np.mean([x["harm"] > alpha for x in rows])),
                             "real_errors_fixed": float(np.mean([x["real_errors_fixed"] for x in rows])),
                             "certified_share": float(np.mean([x.get("lam", 0) >= 0 for x in rows])),
                             "lambda_median": float(np.median([x.get("lam", 0) for x in rows]))}
        tsumm = lambda rows: {k: {m: float(np.mean([t[k][m] for t in rows])) for m in rows[0][k]} for k in rows[0]}
        res[f"in_domain|{alpha}"] = {**summ(ind), "per_type": tsumm(types)}
        res[f"grouped|{alpha}"] = summ(grp)
        if shift:
            res[f"shift|{alpha}"] = {**summ(shift), "per_type": tsumm(types_shift)}
    out[gran] = res
    print(f"\n== {gran}: {res['incidents']} incidents, mix {res['mix']}")
    for k, v in res.items():
        if isinstance(v, dict) and "auto_rate" in v:
            print(f"  {k:18s} auto {v['auto_rate']:.3f} harm {v['harm']:.4f} max {v.get('harm_max', 0):.4f} exceed {v.get('exceed_share', 0):.2f} fixed {v['real_errors_fixed']:.3f} cert {v.get('certified_share', 1):.2f}")
            for t, d in v.get("per_type", {}).items():
                print(f"      {t:18s} n={d['n']:.0f} esc {d['escalated']:.2f} benign {d['benign_right']:.2f}/{d['benign_wrong']:.2f} repair {d['repair_right']:.2f}/{d['repair_wrong']:.2f} harm {d['harm']:.3f} correct avail {d['correct_available']:.2f}")
json.dump(out, open("results/hard_summary.json", "w"), indent=1)
