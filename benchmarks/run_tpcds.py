"""Run the patrol loop on DQ-Repair instances built from TPC-DS store_sales.

Usage: python benchmarks/run_tpcds.py [scale_factor=1] [injections_per_window=3] [db path]
Writes results/tpcds_records.jsonl.gz, one line per incident with every candidate's evidence.
"""
import gzip
import json
import sys
import time
from multiprocessing import Pool

from dataranger.bench import inject
from dataranger.bench.tpcds import load, windows
from dataranger.repair.agent import run

SF = float(sys.argv[1]) if len(sys.argv) > 1 else 1
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
DB = sys.argv[3] if len(sys.argv) > 3 else None
INSTANCES = None


def one(args):
    w, rep = args
    inst = INSTANCES[w]
    t0 = time.time()
    recs = run(inject(inst, seed=10_000 + 100 * w + rep))
    for r in recs:
        r.pop("context", None)
        r.update({"size": "tpcds", "window": w, "rep": rep, "seed": 100 * w + rep, "start": inst.meta["start"],
                  "seconds": time.time() - t0})
    return recs


if __name__ == "__main__":
    facts, customers = load(SF, DB)
    INSTANCES = windows(facts, customers)
    print(f"{len(facts):,} rows, {len(INSTANCES)} windows of 60 days, {REPS} injections each")
    with Pool() as pool:
        recs = [r for rs in pool.map(one, [(w, k) for w in range(len(INSTANCES)) for k in range(REPS)]) for r in rs]
    with gzip.open("results/tpcds_records.jsonl.gz", "wt") as f:
        for r in recs:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"{len(recs)} records")
