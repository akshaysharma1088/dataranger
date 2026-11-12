"""Harder error families (Section 6.8): compound errors, repeated out-of-order adjustments, wrong data that
preserves every count and sum, and partitions whose control totals were never published.

Two passes over the same 150 small instances (the main experiment's seeds), each with the hard mix
and 5% missing manifests:
  channel           the source publishes totals per partition and channel, as in the main experiment
  channel+category  the source also publishes totals per channel and category
Writes results/hard_records.jsonl.gz. Usage: python benchmarks/run_hard.py [n_small=150]
"""
import gzip
import json
import sys
from multiprocessing import Pool

from dataranger.bench import generate, inject
from dataranger.bench.inject import HARD_MIX
from dataranger.repair.agent import run


def one(args):
    gran, seed = args
    case = inject(generate(seed, "small"), mix=HARD_MIX, p_missing=0.05, category_totals=(gran == "channel+category"))
    recs = run(case, granularity=gran)
    for r in recs:
        r.pop("context", None)
        r.update({"size": "small", "seed": seed, "granularity": gran})
    return recs


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    jobs = [(g, s) for g in ("channel", "channel+category") for s in range(1, n + 1)]
    with Pool() as pool, gzip.open("results/hard_records.jsonl.gz", "wt") as f:
        for i, recs in enumerate(pool.imap_unordered(one, jobs), 1):
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
            if i % 50 == 0:
                print(f"{i}/{len(jobs)}", flush=True)
