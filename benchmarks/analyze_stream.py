"""Repair memory under drift: static calibration vs epoch recalibration vs epoch recalibration with memory.

Months 1 to 3 warm up. From month 4 each policy runs; epoch policies recalibrate at the start
of every month on the previous `WINDOW` months of labeled incidents. Writes results/stream_summary.json.
"""
import gzip
import json
from collections import defaultdict

import numpy as np

from dataranger.repair import memory as M
from dataranger.repair.gate import calibrate

WINDOW, DELTA, START, MONTHS = 3, 0.05, 4, 12
recs = [json.loads(l) for l in gzip.open("results/stream_records.jsonl.gz", "rt")]
by = defaultdict(lambda: defaultdict(list))
for r in recs:
    by[r["stream"]][r["month"]].append(r)
streams = sorted(by)


def score(month_recs, lam, mem):
    inc = [r for r in month_recs if not r["table_level"]]
    applied = [M.decide(r, lam, mem) for r in inc]
    harm = np.mean([a is not None and not a["correct"] for a in applied])
    auto = np.mean([a is not None for a in applied])
    pl = [a for r, a in zip(inc, applied) if "partial_late" in r["kinds"]]
    pl_harm = np.mean([a is not None and not a["correct"] for a in pl]) if pl else float("nan")
    return float(harm), float(auto), float(pl_harm)


out = {}
for alpha in (0.02, 0.05):
    res = {p: defaultdict(list) for p in ("static", "epoch", "epoch_memory", "epoch_memory_tolerant", "epoch_memory_frozen")}
    for s in streams:
        months = by[s]
        mem_at, tol_at = {}, {}
        mem, tol = M.Memory(), M.Memory(max_wrong=alpha / 2)
        for m in range(1, MONTHS + 1):
            mem_at[m], tol_at[m] = mem.snapshot(), tol.snapshot()
            mem.learn(months[m])
            tol.learn(months[m])
        lam_static = calibrate([r for m in range(1, START) for r in months[m]], alpha, DELTA)
        for m in range(START, MONTHS + 1):
            window = [r for k in range(m - WINDOW, m) for r in months[k]]
            lam_epoch = calibrate(window, alpha, DELTA)
            lam_mem = M.calibrate(window, [mem_at[k] for k in range(m - WINDOW, m) for _ in months[k]], alpha, DELTA)
            lam_tol = M.calibrate(window, [tol_at[k] for k in range(m - WINDOW, m) for _ in months[k]], alpha, DELTA)
            # Frozen memory: learned only before the window, then held fixed for calibration and deployment,
            # so the certified policy is exactly the deployed one and the LTT bound applies as stated.
            frozen = mem_at[m - WINDOW]
            lam_frz = M.calibrate(window, [frozen] * len(window), alpha, DELTA)
            for p, lam, mm in (("static", lam_static, None), ("epoch", lam_epoch, None),
                               ("epoch_memory", lam_mem, mem_at[m]), ("epoch_memory_tolerant", lam_tol, tol_at[m]),
                               ("epoch_memory_frozen", lam_frz, frozen)):
                h, a, ph = score(months[m], lam, mm)
                res[p][m].append({"harm": h, "auto": a, "pl_harm": ph, "lam": lam})
    summ = {}
    for p, d in res.items():
        summ[p] = {m: {"harm": float(np.mean([x["harm"] for x in v])), "auto": float(np.mean([x["auto"] for x in v])),
                       "partial_late_harm": float(np.nanmean([x["pl_harm"] for x in v])),
                       "violations": float(np.mean([x["harm"] > alpha for x in v])),
                       "lambda": float(np.median([x["lam"] for x in v]))} for m, v in sorted(d.items())}
        allv = [x for v in d.values() for x in v]
        summ[p]["overall"] = {"harm": float(np.mean([x["harm"] for x in allv])),
                              "auto": float(np.mean([x["auto"] for x in allv])),
                              "violations": float(np.mean([x["harm"] > alpha for x in allv]))}
    out[str(alpha)] = summ

json.dump(out, open("results/stream_summary.json", "w"), indent=1)
for alpha, summ in out.items():
    print(f"\nalpha {alpha}")
    for p, d in summ.items():
        line = " ".join(f"{d[m]['harm']:.3f}/{d[m]['auto']:.2f}" for m in range(START, MONTHS + 1))
        o = d["overall"]
        print(f"  {p:13s} harm/auto by month: {line}")
        print(f"  {'':13s} overall harm {o['harm']:.4f} auto {o['auto']:.3f} month-violations {o['violations']:.2f}; "
              f"partial-late harm m5 {d[5]['partial_late_harm']:.2f} m6 {d[6]['partial_late_harm']:.2f}")
