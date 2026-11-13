"""Independent incident population for the cohort experiment (Q1b).

Generates fresh instances whose seeds never overlap the main benchmark (seeds 1 to 150 small, 1 to
100 medium), so every instance is an independent draw from the generator. analyze_cohorts.py cuts
them into disjoint calibration cohorts and one large held-out test population.

Usage: python benchmarks/run_cohorts.py [n_small] [n_medium] [output]
Records omit the per-incident context block, which is only needed for the language-model runs.
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

from dataranger.bench import generate, inject
from dataranger.repair.agent import run

SEED_BASE = {"small": 10_000, "medium": 20_000}


def one(args):
    size, seed = args
    inst = generate(seed, size)
    recs = run(inject(inst))
    for r in recs:
        r.pop("context", None)
        r.update({"size": size, "seed": seed})
    return recs


if __name__ == "__main__":
    n_small = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    n_medium = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    output = sys.argv[3] if len(sys.argv) > 3 else "results/cohort_records.jsonl"
    start = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    jobs = [("small", SEED_BASE["small"] + s) for s in range(start, n_small + 1)] + \
        [("medium", SEED_BASE["medium"] + s) for s in range(1, n_medium + 1)]
    Path("results").mkdir(exist_ok=True)
    with Pool() as pool, open(output, "a" if start > 1 else "w") as f:
        for i, recs in enumerate(pool.imap_unordered(one, jobs, chunksize=4), 1):
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
            if i % 100 == 0:
                print(f"{i}/{len(jobs)} instances", flush=True)
