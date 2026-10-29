"""How DataRanger's cost grows with data size.

Part 1, patrols: the whole loop (inject, detect, propose, verify every candidate) on TPC-DS
store_sales windows at the scale factors whose DuckDB files are given, for 60-day and 365-day windows.
Part 2, one partition: the cost of verifying candidate fixes on a single daily partition of 1 to
20 million rows, the size range of the card pipeline that motivates the paper. Each candidate is
applied to a copy, its invariants are checked and its residual against the control totals computed.

Usage: python benchmarks/scale.py sf1.duckdb:1 sf10.duckdb:10
Writes results/scale_summary.json. Times are wall-clock on the machine that runs it.
"""
import json
import resource
import sys
import time

import numpy as np
import pandas as pd

from dataranger.bench import inject
from dataranger.bench.tpcds import load, windows
from dataranger.repair import fixes as F
from dataranger.repair.agent import invariants_hold, run
from dataranger.repair.verify import residual


def peak_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # Linux reports kilobytes


out = {"patrols": [], "partition": []}
for arg in sys.argv[1:]:
    db, sf = arg.rsplit(":", 1)
    facts, customers = load(float(sf), db)
    for n_days in (60, 365):
        ws = windows(facts, customers, n_days=n_days)
        inst = ws[len(ws) // 2]
        t0 = time.time()
        recs = run(inject(inst, seed=7))
        secs = time.time() - t0
        row = {"sf": float(sf), "days": n_days, "rows": int(len(inst.source_txn)),
               "rows_per_day": float(len(inst.source_txn) / len(inst.days)),
               "incidents": int(sum(not r["table_level"] for r in recs)),
               "candidates": int(sum(len(r["candidates"]) for r in recs)),
               "seconds": secs, "peak_gb": peak_gb()}
        out["patrols"].append(row)
        print(row, flush=True)
    del facts

rng = np.random.default_rng(0)
for n in (1_000_000, 5_000_000, 10_000_000, 20_000_000):
    d = pd.Timestamp("2026-03-01").date()
    part = pd.DataFrame({"txn_id": np.arange(n, dtype=np.int64),
                         "customer_id": rng.integers(1, 1_000_001, n).astype(np.int64),
                         "txn_date": d,
                         "channel": pd.Categorical(rng.choice(["card", "ach", "wire", "p2p"], n, p=[0.6, 0.2, 0.05, 0.15])),
                         "category": pd.Categorical(rng.choice(["grocery", "fuel", "travel", "dining"], n)),
                         "amount": np.round(rng.lognormal(3.8, 0.6, n), 2)})
    customers = pd.DataFrame({"customer_id": np.arange(1, 1_000_001, dtype=np.int64)})
    g = part.groupby("channel", observed=True).agg(n=("txn_id", "size"), amount=("amount", "sum")).reset_index()
    man = pd.concat([pd.DataFrame({"txn_date": [d], "channel": ["*"], "n": [n], "amount": [part["amount"].sum()]}),
                     g.assign(txn_date=d)], ignore_index=True)
    man["channel"] = man["channel"].astype(str)
    dirty = pd.concat([part, part.sample(frac=0.01, random_state=1)], ignore_index=True)  # 1% re-sent rows
    times = {}
    for fx in (F.make("none"), F.make("dedupe", key="txn_id"), F.make("dedupe", key="txn_id", keep="last"),
               F.make("rescale", factor=0.01, channel="ach")):
        t0 = time.time()
        fixed = F.apply(fx, dirty, None, customers)
        ok = invariants_hold(fixed, customers)
        res = residual(fixed, man, d) if ok else float("inf")
        times[str(fx)] = {"seconds": time.time() - t0, "residual": res}
    row = {"rows": n, "per_candidate_seconds": float(np.mean([v["seconds"] for v in times.values()])),
           "candidates": times, "peak_gb": peak_gb()}
    out["partition"].append(row)
    print({k: v for k, v in row.items() if k != "candidates"}, flush=True)
    del part, dirty, fixed

json.dump(out, open("results/scale_summary.json", "w"), indent=1, default=str)
