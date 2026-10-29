"""Ablations and sensitivity studies for the paper.

  size     : automation and harm as the calibration set grows (instances, then incidents)
  delta    : sensitivity to the confidence level delta
  stale    : how stale control totals change the trade-off (reruns the loop)
  invariant: verification without the invariant checks (reruns the loop)
  runtime  : wall-clock seconds per patrol by table size
Writes results/ablations.json.  Usage: python benchmarks/ablations.py [which ...]
"""
import gzip
import json
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from dataranger.bench import generate, inject
from dataranger.repair.agent import run
from dataranger.repair.gate import calibrate, evaluate

OUT = Path("results/ablations.json")
out = json.load(open(OUT)) if OUT.exists() else {}
which = set(sys.argv[1:]) or {"size", "delta", "stale", "invariant", "runtime"}


def load():
    recs = [json.loads(l) for l in gzip.open("results/repair_records.jsonl.gz", "rt")]
    by = defaultdict(list)
    for r in recs:
        by[(r["size"], r["seed"])].append(r)
    return by


def split_eval(by, n_cal, alpha, delta, splits=50, seed=0):
    keys = sorted(by)
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(splits):
        perm = rng.permutation(len(keys))
        cal = [r for i in perm[:n_cal] for r in by[keys[i]]]
        test = [r for i in perm[len(keys) // 2:] for r in by[keys[i]]]
        lam = calibrate(cal, alpha, delta)
        e = evaluate(test, "verified", lam)
        rows.append(e | {"n_cal_incidents": sum(not r["table_level"] for r in cal)})
    return {"auto": float(np.mean([x["auto_rate"] for x in rows])), "harm": float(np.mean([x["harm"] for x in rows])),
            "violations": float(np.mean([x["harm"] > alpha for x in rows])),
            "incidents": float(np.mean([x["n_cal_incidents"] for x in rows]))}


def one_case(args):
    seed, p_stale, inv = args
    recs = run(inject(generate(seed, "small"), p_stale=p_stale), check_invariants=inv)
    for r in recs:
        r.update({"size": "small", "seed": seed})
    return recs


def rerun(p_stale, inv, n=80):
    with Pool() as p:
        recs = [r for rs in p.map(one_case, [(s, p_stale, inv) for s in range(1, n + 1)]) for r in rs]
    by = defaultdict(list)
    for r in recs:
        by[r["seed"]].append(r)
    return by


if "size" in which:
    by = load()
    out["size"] = {a: {n: split_eval(by, n, a, 0.05) for n in (5, 10, 20, 40, 80, 125)} for a in (0.01, 0.05)}
    out["size"] = {str(a): {str(n): v for n, v in d.items()} for a, d in out["size"].items()}
if "delta" in which:
    by = load()
    out["delta"] = {str(d): split_eval(by, 125, 0.02, d) for d in (0.01, 0.05, 0.1, 0.2)}
if "stale" in which:
    out["stale"] = {}
    for ps in (0.0, 0.15, 0.3, 0.5):
        by = rerun(ps, True)
        out["stale"][str(ps)] = {str(a): split_eval(by, 40, a, 0.05) for a in (0.01, 0.05)}
if "invariant" in which:
    by_off = rerun(0.15, False)
    by_on = rerun(0.15, True)
    out["invariant"] = {"with": {str(a): split_eval(by_on, 40, a, 0.05) for a in (0.01, 0.05)},
                        "without": {str(a): split_eval(by_off, 40, a, 0.05) for a in (0.01, 0.05)}}
if "runtime" in which:
    out["runtime"] = {}
    for size in ("small", "medium", "large"):
        ts = []
        for seed in (1, 2, 3):
            c = inject(generate(seed, size))
            t = time.time()
            recs = run(c)
            ts.append({"seconds": time.time() - t, "rows": len(c.warehouse["transactions"]),
                       "incidents": sum(not r["table_level"] for r in recs)})
        out["runtime"][size] = {k: float(np.mean([x[k] for x in ts])) for k in ts[0]}

json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out, indent=1))
