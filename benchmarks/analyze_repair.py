"""Guarantee experiment on the incident records from run_repair.py.

For 100 random splits of instances into calibration and test halves, calibrate the gate
with Learn-then-Test and measure silent harm, automation and validity on the test half.
Baselines use no calibration. Writes results/repair_summary.json.
"""
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from dataranger.repair.gate import calibrate, decide, evaluate, loss

DECOYS = {"volume_spike", "big_ticket_day"}
src = Path("results/repair_records.jsonl")
recs = [json.loads(l) for l in (open(src) if src.exists() else gzip.open(f"{src}.gz", "rt"))]
inc = [r for r in recs if not r["table_level"]]
by_inst = defaultdict(list)
for r in recs:
    by_inst[(r["size"], r["seed"])].append(r)
keys = sorted(by_inst)
rng = np.random.default_rng(0)
SPLITS, DELTA = 100, 0.05
out = {"instances": len(keys), "incidents": len(inc), "table_level": len(recs) - len(inc)}


def kind_of(r):
    real = [k for k in r["kinds"] if k != "false_alarm"]
    return "+".join(sorted(set(real))) if real else "false_alarm"


# Incident mix and how often the best-supported candidate is truly correct.
mix = defaultdict(lambda: {"n": 0, "correct_available": 0, "best_correct": 0})
from dataranger.repair.gate import select
for r in inc:
    m = mix[kind_of(r)]
    m["n"] += 1
    m["correct_available"] += any(c["correct"] for c in r["candidates"])
    b = select(r)
    m["best_correct"] += bool(b and b["correct"])
out["mix"] = dict(mix)

# Policies on all incidents (no calibration needed for the baselines).
out["all"] = {name: evaluate(inc, pol, lam) for name, pol, lam in [
    ("rules", "rules", None), ("monitors", "monitors", None),
    ("verified_0", "verified", 0.0), ("verified_0.01", "verified", 0.01), ("verified_0.05", "verified", 0.05)]}

# Calibration experiment.
splits = []
for _ in range(SPLITS):
    perm = rng.permutation(len(keys))
    cal_k = {keys[i] for i in perm[: len(keys) // 2]}
    splits.append(([r for k in keys if k in cal_k for r in by_inst[k]],
                   [r for k in keys if k not in cal_k for r in by_inst[k]]))

res = {}
for evidence in ("residual", "residual_partition"):
    for alpha in (0.01, 0.02, 0.05):
        rows = []
        for cal, test in splits:
            lam = calibrate(cal, alpha, DELTA, res=evidence)
            e = evaluate(test, "verified", lam, res=evidence)
            rows.append({**e, "lambda": lam, "violated": e["harm"] > alpha})
        res[f"{evidence}|{alpha}"] = {
            "alpha": alpha, "delta": DELTA, "evidence": evidence,
            "lambda_median": float(np.median([x["lambda"] for x in rows])),
            "harm_mean": float(np.mean([x["harm"] for x in rows])),
            "harm_max": float(np.max([x["harm"] for x in rows])),
            "auto_rate_mean": float(np.mean([x["auto_rate"] for x in rows])),
            "real_errors_fixed_mean": float(np.mean([x["real_errors_fixed"] for x in rows])),
            "violation_rate": float(np.mean([x["violated"] for x in rows])),
        }
out["calibrated"] = res

# Fixed thresholds: how often would a hand-picked threshold break a target?
fixed = {}
for evidence in ("residual", "residual_partition"):
    for lam in (0.005, 0.01, 0.02, 0.05, 0.1):
        hs = [evaluate(test, "verified", lam, res=evidence)["harm"] for _, test in splits]
        fixed[f"{evidence}|{lam}"] = {"harm_mean": float(np.mean(hs)), "harm_max": float(np.max(hs)),
                                      "violates_0.01": float(np.mean([h > 0.01 for h in hs])),
                                      "auto_rate": evaluate(inc, "verified", lam, res=evidence)["auto_rate"]}
out["fixed_threshold"] = fixed

# Held-out incident types: calibrate without type K, then measure harm on type K alone.
held = {}
for K in ("partial_late", "silent_drop", "rekeyed_duplicates", "duplicates", "unit_scale"):
    hs, ar = [], []
    for cal, test in splits[:30]:
        lam = calibrate([r for r in cal if K not in r["kinds"]], 0.05, DELTA)
        tk = [r for r in test if K in r["kinds"]]
        e = evaluate(tk, "verified", lam)
        hs.append(e["harm"])
        ar.append(e["auto_rate"])
    held[K] = {"harm_on_type_mean": float(np.mean(hs)), "auto_rate_on_type": float(np.mean(ar))}
out["held_out_type_alpha_0.05"] = held

json.dump(out, open("results/repair_summary.json", "w"), indent=1)
print(f"{out['instances']} instances, {out['incidents']} incidents")
for k, v in sorted(mix.items()):
    print(f"  {k:28s} n={v['n']:4d} correct available {v['correct_available']/v['n']:.2f} best correct {v['best_correct']/v['n']:.2f}")
for k, v in out["all"].items():
    print(f"  {k:16s} auto {v['auto_rate']:.3f} harm {v['harm']:.3f} real fixed {v['real_errors_fixed']:.3f}")
for k, v in res.items():
    print(f"  LTT {k:28s} lam {v['lambda_median']:.4f} harm {v['harm_mean']:.4f} (max {v['harm_max']:.4f}) "
          f"auto {v['auto_rate_mean']:.3f} fixed {v['real_errors_fixed_mean']:.3f} violations {v['violation_rate']:.2f}")
for k, v in fixed.items():
    print(f"  fixed {k:28s} harm {v['harm_mean']:.4f} (max {v['harm_max']:.4f}) auto {v['auto_rate']:.3f} P(harm>1%) {v['violates_0.01']:.2f}")
for k, v in held.items():
    print(f"  held-out {k:20s} harm {v['harm_on_type_mean']:.3f} auto {v['auto_rate_on_type']:.3f}")
