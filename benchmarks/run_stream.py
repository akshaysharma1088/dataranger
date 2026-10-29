"""A stream of months with drift, for the repair-memory experiment.

Each month a data estate produces incidents from `tables` tables. Partial-late incidents
start in month 5; manifests get staler (30% instead of 15%) from month 9.
Writes results/stream_records.jsonl.gz.  Usage: python benchmarks/run_stream.py [streams] [tables]
"""
import gzip
import json
import sys
from multiprocessing import Pool

from dataranger.bench import generate, inject
from dataranger.bench.inject import DEFAULT_MIX
from dataranger.repair.agent import run

MONTHS = 12


def month_config(m):
    mix = dict(DEFAULT_MIX)
    if m < 5:
        mix["partial_late"] = 0
    return mix, (0.30 if m >= 9 else 0.15)


def one(job):
    stream, month, table = job
    seed = stream * 10000 + month * 100 + table
    mix, p_stale = month_config(month)
    recs = run(inject(generate(seed, "small", n_days=50), mix=mix, p_stale=p_stale))
    for r in recs:
        r.update({"stream": stream, "month": month, "table": table})
    return recs


if __name__ == "__main__":
    streams = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    tables = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    jobs = [(s, m, t) for s in range(1, streams + 1) for m in range(1, MONTHS + 1) for t in range(tables)]
    with Pool() as pool, gzip.open("results/stream_records.jsonl.gz", "wt") as f:
        for i, recs in enumerate(pool.imap_unordered(one, jobs), 1):
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
            if i % 50 == 0:
                print(f"{i}/{len(jobs)}", flush=True)
