"""Deterministic detectors (stage 1 of Detect, Fix, Prove).

Detectors see only the warehouse and its contract, never the source. They use SQL on
DuckDB plus simple robust statistics, and no language model. A finding is a candidate:
some findings are real errors, some are decoys that the later stages must leave alone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

import duckdb
import numpy as np
import pandas as pd

from .tables import TXN_SCHEMA


@dataclass
class Finding:
    detector: str
    table: str
    partition: str | None
    score: float
    evidence: dict = field(default_factory=dict)


def _iso(d) -> str:
    return pd.Timestamp(d).date().isoformat()


def detect(warehouse: dict[str, pd.DataFrame], expected_days: list[date],
           z_volume: float = 4.0, z_mix: float = 5.0, scale_ratio: float = 20.0,
           null_jump: float = 0.5) -> list[Finding]:
    con = duckdb.connect()
    t = warehouse["transactions"]
    con.register("txn", t)
    con.register("customers", warehouse["customers"])
    con.register("cal", pd.DataFrame({"d": pd.to_datetime(pd.Series(expected_days)).dt.date}))
    out: list[Finding] = []

    # Schema contract: unexpected or missing columns.
    cols = set(t.columns)
    extra, missing = sorted(cols - set(TXN_SCHEMA)), sorted(set(TXN_SCHEMA) - cols)
    if extra or missing:
        out.append(Finding("schema_contract", "transactions", None, 1.0, {"unexpected": extra, "missing": missing}))

    # Null-rate jump in a contracted column, located at the first affected partition.
    for c in [c for c in TXN_SCHEMA if c in cols and c != "txn_date"]:
        nr = con.execute(f"select txn_date, avg(case when {c} is null then 1.0 else 0.0 end) r "
                         f"from txn group by 1 order by 1").df()
        hit = nr[nr["r"] >= null_jump]
        if len(hit) and (nr["r"] < 0.01).any():
            out.append(Finding("null_spike", "transactions", _iso(hit["txn_date"].iloc[0]), float(hit["r"].mean()),
                               {"column": c, "partitions": len(hit)}))

    # Duplicate business keys.
    for d, n in con.execute("select txn_date, count(*) - count(distinct txn_id) from txn "
                            "group by 1 having count(*) > count(distinct txn_id) order by 1").fetchall():
        out.append(Finding("duplicate_keys", "transactions", _iso(d), float(n), {"extra_rows": int(n)}))

    # Missing partitions against the expected calendar.
    for (d,) in con.execute("select d from cal anti join (select distinct txn_date from txn) p "
                            "on cal.d = p.txn_date order by 1").fetchall():
        out.append(Finding("missing_partition", "transactions", _iso(d), 1.0, {}))

    # Orphan foreign keys.
    for d, n in con.execute("select t.txn_date, count(*) from txn t anti join customers c "
                            "on t.customer_id = c.customer_id group by 1 order by 1").fetchall():
        out.append(Finding("orphan_keys", "transactions", _iso(d), float(n), {"rows": int(n)}))

    # Volume against same-weekday partitions (robust z on log counts).
    vol = con.execute("select txn_date, count(*) n, isodow(txn_date) w from txn group by 1, 3").df()
    vol["ln"] = np.log(vol["n"])
    for w, g in vol.groupby("w"):
        med = g["ln"].median()
        mad = 1.4826 * (g["ln"] - med).abs().median() or 0.02
        for _, r in g.iterrows():
            z = (r["ln"] - med) / max(mad, 0.02)
            if abs(z) >= z_volume:
                out.append(Finding("volume_drop" if z < 0 else "volume_rise", "transactions", _iso(r["txn_date"]),
                                   float(z), {"rows": int(r["n"]), "typical": int(round(np.exp(med)))}))

    # Unit or scale shift: per-channel median amount far from its usual level.
    amt = con.execute("select txn_date, channel, median(amount) m, avg(amount) a, count(*) n "
                      "from txn where channel is not null group by 1, 2").df()
    for ch, g in amt.groupby("channel"):
        base = g["m"].median()
        for _, r in g.iterrows():
            ratio = r["m"] / base if base else 1.0
            if ratio >= scale_ratio or ratio <= 1 / scale_ratio:
                out.append(Finding("scale_shift", "transactions", _iso(r["txn_date"]), float(ratio),
                                   {"channel": ch, "median": float(r["m"]), "usual": float(base)}))
    day = con.execute("select txn_date, avg(amount) a from txn group by 1").df()
    base = day["a"].median()
    flagged = {f.partition for f in out if f.detector == "scale_shift"}
    for _, r in day.iterrows():
        if r["a"] / base >= scale_ratio / 4 and _iso(r["txn_date"]) not in flagged:
            out.append(Finding("scale_shift", "transactions", _iso(r["txn_date"]), float(r["a"] / base),
                               {"mean": float(r["a"]), "usual": float(base)}))

    # Mix shift: a category or channel whose share of the partition moves far from usual.
    for col in ("category", "channel"):
        mix = con.execute(f"select txn_date, {col} v, count(*) n, sum(count(*)) over (partition by txn_date) tot "
                          f"from txn where {col} is not null group by 1, 2").df()
        if mix.empty:
            continue
        days_all = mix["txn_date"].unique()
        full = mix.pivot_table(index="txn_date", columns="v", values="n", fill_value=0)
        tot = full.sum(axis=1)
        share = full.div(tot, axis=0)
        base_share = share.median()
        for d in days_all:
            n = tot[d]
            for v in share.columns:
                p0 = base_share[v]
                if p0 <= 0:
                    continue
                z = (share.at[d, v] - p0) / np.sqrt(p0 * (1 - p0) / n)
                if abs(z) >= z_mix:
                    out.append(Finding("mix_drop" if z < 0 else "mix_rise", "transactions", _iso(d), float(z),
                                       {"column": col, "value": v, "share": round(float(share.at[d, v]), 4),
                                        "usual": round(float(p0), 4)}))
    con.close()
    return out


# Which detector finding counts as a correct catch for which issue type.
CATCHES = {
    "duplicates": {"duplicate_keys"},
    "late_partition": {"missing_partition"},
    "schema_drift": {"schema_contract", "null_spike"},
    "broken_refs": {"orphan_keys"},
    "unit_scale": {"scale_shift"},
    "silent_drop": {"volume_drop", "mix_drop"},
}


def score(findings: list[Finding], issues: list) -> dict:
    """Detection recall per error type, decoys flagged, and false alarms on clean partitions."""
    by_part: dict[str | None, list[Finding]] = {}
    for f in findings:
        by_part.setdefault(f.partition, []).append(f)
    rows = []
    for i in issues:
        if i.partition is None:
            hit = [f for f in findings if f.detector in CATCHES.get(i.kind, set())]
        else:
            hit = by_part.get(i.partition, [])
        strict = any(f.detector in CATCHES.get(i.kind, set()) for f in hit)
        rows.append({"issue": i.issue_id, "kind": i.kind, "decoy": i.decoy, "flagged": bool(hit),
                     "caught": strict if not i.decoy else None, "detectors": sorted({f.detector for f in hit})})
    issue_parts = {i.partition for i in issues}
    drift_since = next((i.fix["since"] for i in issues if i.kind == "schema_drift"), None)
    false_alarms = [asdict(f) for f in findings if f.partition is not None and f.partition not in issue_parts
                    and not (f.detector == "null_spike" and drift_since and f.partition >= drift_since)]
    return {"issues": rows, "false_alarms": false_alarms}
