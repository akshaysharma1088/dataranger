"""Corrupt control totals: the source publishes totals computed downstream of the error.

For each corruption rate, 150 small instances (the same seeds as run_repair.py) are injected with
`p_corrupt` set, so that share of errored partitions gets control totals that agree with the wrong
data. Rate 0 reuses the main experiment's small instances. Writes results/corrupt_records.jsonl.gz.
Usage: python benchmarks/run_corrupt.py [n_small=150]
"""
import gzip
import json
import sys
from multiprocessing import Pool

from dataranger.bench import generate, inject
from dataranger.repair.agent import run

RATES = (0.05, 0.2)


def one(args):
    p, seed = args
    recs = run(inject(generate(seed, "small"), p_corrupt=p))
    for r in recs:
        r.pop("context", None)
        r.update({"size": "small", "seed": seed, "p_corrupt": p})
    return recs


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    with Pool() as pool:
        recs = [r for rs in pool.map(one, [(p, s) for p in RATES for s in range(1, n + 1)]) for r in rs]
    with gzip.open("results/corrupt_records.jsonl.gz", "wt") as f:
        for r in recs:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"{len(recs)} records")
