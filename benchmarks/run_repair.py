"""Stage 2 to 5: propose, verify and gate. Writes one record per incident to results/repair_records.jsonl.

Usage: python benchmarks/run_repair.py [n_small] [n_medium]
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

from dataranger.bench import generate, inject
from dataranger.repair.agent import run


def one(args):
    size, seed = args
    inst = generate(seed, size)
    recs = run(inject(inst))
    for r in recs:
        r.update({"size": size, "seed": seed})
    return recs


if __name__ == "__main__":
    n_small = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    n_medium = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    jobs = [("small", s) for s in range(1, n_small + 1)] + [("medium", s) for s in range(1, n_medium + 1)]
    Path("results").mkdir(exist_ok=True)
    with Pool() as pool, open("results/repair_records.jsonl", "w") as f:
        for i, recs in enumerate(pool.imap_unordered(one, jobs), 1):
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
            if i % 25 == 0:
                print(f"{i}/{len(jobs)} instances", flush=True)
