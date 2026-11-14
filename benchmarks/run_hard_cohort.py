"""Independent instances with the harder error mix, for cluster-level certification (Section 6.8).

Fresh seeds (30001 onward), so every instance is an independent draw. Resumable: appends to
results/hard_cohort_records.jsonl and skips (granularity, seed) pairs already written.
Usage: python benchmarks/run_hard_cohort.py [n=500] [granularities=channel+category,channel] [output]
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

from dataranger.bench import generate, inject
from dataranger.bench.inject import HARD_MIX
from dataranger.repair.agent import run

OUT = Path("results/hard_cohort_records.jsonl")


def one(args):
    gran, seed = args
    case = inject(generate(seed, "small"), mix=HARD_MIX, p_missing=0.05, category_totals=(gran == "channel+category"))
    recs = run(case, granularity=gran)
    for r in recs:
        r.pop("context", None)
        r.update({"size": "small", "seed": seed, "granularity": gran})
    return recs


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    grans = (sys.argv[2] if len(sys.argv) > 2 else "channel+category,channel").split(",")
    if len(sys.argv) > 3:
        OUT = Path(sys.argv[3])
    done = set()
    if OUT.exists():
        for l in open(OUT):
            try:
                r = json.loads(l); done.add((r["granularity"], r["seed"]))
            except json.JSONDecodeError:
                pass
    jobs = [(g, 30000 + s) for g in grans for s in range(1, n + 1) if (g, 30000 + s) not in done]
    if len(sys.argv) > 4 and sys.argv[4] == "reverse":   # lets two processes meet in the middle
        jobs = jobs[::-1]
    with Pool(1) as pool, open(OUT, "a") as f:
        for i, recs in enumerate(pool.imap(one, jobs), 1):
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
            f.flush()
            if i % 25 == 0:
                print(f"{i}/{len(jobs)}", flush=True)
