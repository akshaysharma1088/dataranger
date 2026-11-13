"""Verification pushed down into the warehouse engine: one daily partition of 1 to 50 million rows.

The pandas implementation (scale.py) applies each candidate to a copy of the partition in client
memory. Nothing in verification needs that: a candidate fix is a relational expression over the
partition, the invariants are a count, a distinct count and an anti join, and the residual is a
group-by compared with the manifest. Here each candidate is a view over the stored partition and
all three checks run as SQL in DuckDB, so no copy of the unchanged rows is ever materialised. Same
candidates and same 1% re-sent rows as scale.py; the 50-million-row case is the opening incident.

Usage: python benchmarks/pushdown.py [db_path=/tmp/pushdown.duckdb]   (needs ~6 GB of disk)
Writes results/pushdown_summary.json. Times are wall-clock on the machine that runs it.
"""
import json
import os
import resource
import sys
import time

import duckdb

db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pushdown.duckdb"
SIZES = (1_000_000, 5_000_000, 10_000_000, 20_000_000, 50_000_000)
FIXES = {
    "none()": "select * from dirty",
    "dedupe(key=txn_id)": "select * from dirty qualify row_number() over (partition by txn_id order by load_seq) = 1",
    "dedupe(keep=last, key=txn_id)": "select * from dirty qualify row_number() over (partition by txn_id order by load_seq desc) = 1",
    "rescale(channel=ach, factor=0.01)": "select txn_id, customer_id, txn_date, channel, category, "
                                         "case when channel = 'ach' then round(amount * 0.01, 2) else amount end as amount, load_seq from dirty",
}


def peak_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


if os.path.exists(db):
    os.remove(db)
con = duckdb.connect(db)
con.execute("set threads = 2; set memory_limit = '5GB'; set preserve_insertion_order = false")
con.execute("create table customers as select range as customer_id from range(1, 1000001)")
out = []
for n in SIZES:
    t0 = time.time()
    con.execute("drop table if exists part; drop table if exists dirty; drop table if exists manifest")
    con.execute(f"""
        create table part as
        select range as txn_id, (random() * 1000000)::bigint + 1 as customer_id, date '2026-03-01' as txn_date,
               case when u < 0.6 then 'card' when u < 0.8 then 'ach' when u < 0.85 then 'wire' else 'p2p' end as channel,
               ['grocery', 'fuel', 'travel', 'dining'][(random() * 4)::int + 1] as category,
               round(exp(3.8 + 0.6 * sqrt(-2 * ln(greatest(random(), 1e-12))) * cos(2 * pi() * random())), 2) as amount
        from (select range, random() as u from range({n}))""")
    con.execute("""
        create table manifest as
        select '*' as channel, count(*) as n, sum(amount) as amount from part
        union all select channel, count(*), sum(amount) from part group by channel""")
    # 1% re-sent rows, appended after the original load
    con.execute("""
        create table dirty as
        select *, row_number() over () as load_seq from (
            select * from part union all select * from part using sample 1% (bernoulli, 1))""")
    build = time.time() - t0
    rows = con.execute("select count(*) from dirty").fetchone()[0]
    times = {}
    for name, view in FIXES.items():
        t0 = time.time()
        con.execute(f"create or replace view fixed as {view}")
        # invariants: non-empty, unique keys, every reference resolves (one scan plus one anti join)
        nrows, ndist = con.execute("select count(*), count(distinct txn_id) from fixed").fetchone()
        orphans = con.execute("select count(*) from fixed f anti join customers c on f.customer_id = c.customer_id").fetchone()[0]
        ok = nrows > 0 and nrows == ndist and orphans == 0
        res = float("inf")
        if ok:
            res = con.execute("""
                with obs as (select '*' as channel, count(*) as n, sum(amount) as amount from fixed
                             union all select channel, count(*), sum(amount) from fixed group by channel)
                select max(greatest(abs(coalesce(o.n, 0) - m.n) / greatest(m.n, 1),
                                    abs(coalesce(o.amount, 0) - m.amount) / greatest(abs(m.amount), 1.0)))
                from manifest m left join obs o using (channel)""").fetchone()[0]
        times[name] = {"seconds": time.time() - t0, "residual": res, "invariants": ok}
    row = {"rows": int(rows), "build_seconds": build, "per_candidate_seconds": sum(v["seconds"] for v in times.values()) / len(times),
           "candidates": times, "peak_client_gb": peak_gb(), "db_gb": os.path.getsize(db) / 1e9}
    out.append(row)
    print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items() if k != "candidates"},
          {k: round(v["seconds"], 2) for k, v in times.items()}, flush=True)
    json.dump(out, open("results/pushdown_summary.json", "w"), indent=1, default=str)
con.close()
os.remove(db)
