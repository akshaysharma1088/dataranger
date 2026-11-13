"""SQL shared by the connectors: safe identifiers, the three SELECTs, and tidying what comes back."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

_SIMPLE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_QUOTED = re.compile(r'"[^"]+"')


def relation(name: str, what: str = "table") -> str:
    """Check a possibly qualified table name (db.schema.table, parts may be "quoted") and return it unchanged.

    Names go straight into SQL, so anything that is not a plain or quoted identifier is refused.
    """
    parts = re.findall(r'"[^"]*"|[^.]+', name or "")
    if not parts or ".".join(parts) != name or not all(_SIMPLE.fullmatch(p) or _QUOTED.fullmatch(p) for p in parts):
        raise ValueError(f"{what} name {name!r} is not valid: use letters, digits and underscores, "
                         'optionally qualified as database.schema.table, with "quotes" around unusual parts')
    return name


def column(name: str, what: str = "column") -> str:
    if not name or not _SIMPLE.fullmatch(name):
        raise ValueError(f"{what} name {name!r} is not valid: use letters, digits and underscores")
    return name


def cutoff(days: int | None, today: date | None = None) -> date | None:
    """First date to read when only the last `days` days are wanted (today counts as day one)."""
    if days is None:
        return None
    if days < 1:
        raise ValueError("days must be 1 or more")
    return (today or date.today()) - timedelta(days=days - 1)


def build_queries(table: str, ref_table: str, manifest_table: str, mapping, days: int | None = None,
                  today: date | None = None) -> tuple[str, str, str]:
    """The three read-only queries of a patrol. Only the mapped columns are selected, to keep transfers small."""
    since = cutoff(days, today)
    where = f" where {column(mapping.date, 'date column')} >= '{since.isoformat()}'::date" if since else ""
    fact_cols = [mapping.key, mapping.reference, mapping.date, mapping.segment, mapping.amount]
    if mapping.category:
        fact_cols.append(mapping.category)
    fact_cols = list(dict.fromkeys(column(c) for c in fact_cols))
    man_cols = list(dict.fromkeys([column(mapping.date), column(mapping.segment), "n", "amount"]))
    return (
        f"select {', '.join(fact_cols)} from {relation(table, 'fact table')}{where}",
        f"select distinct {column(mapping.ref_key, 'reference key')} from {relation(ref_table, 'reference table')}",
        f"select {', '.join(man_cols)} from {relation(manifest_table, 'control totals table')}{where}",
    )


def _match_case(df: pd.DataFrame, wanted: list[str]) -> pd.DataFrame:
    """Warehouses such as Snowflake return TXN_ID for txn_id. Rename columns to the names the user gave."""
    lower = {str(c).lower(): c for c in df.columns}
    return df.rename(columns={lower[w.lower()]: w for w in wanted if w.lower() in lower and lower[w.lower()] != w})


def normalize(facts: pd.DataFrame, refs: pd.DataFrame, manifest: pd.DataFrame, mapping):
    """Give the frames the column names and numeric types a patrol expects, whatever the warehouse returned."""
    fact_cols = [mapping.key, mapping.reference, mapping.date, mapping.segment, mapping.amount]
    if mapping.category:
        fact_cols.append(mapping.category)
    facts = _match_case(facts, fact_cols)
    refs = _match_case(refs, [mapping.ref_key])
    manifest = _match_case(manifest, [mapping.date, mapping.segment, "n", "amount"])
    if mapping.amount in facts:  # NUMBER columns can arrive as Decimal objects
        facts[mapping.amount] = pd.to_numeric(facts[mapping.amount], errors="coerce").astype(float)
    for col in ("n", "amount"):
        if col in manifest:
            manifest[col] = pd.to_numeric(manifest[col], errors="coerce").astype(float)
    return facts, refs, manifest


def filter_days(facts: pd.DataFrame, manifest: pd.DataFrame, mapping, days: int | None, today: date | None = None):
    """The same last-N-days window for sources that are read whole (DuckDB files, CSV, Parquet)."""
    since = cutoff(days, today)
    if since is None or mapping.date not in facts or mapping.date not in manifest:
        return facts, manifest
    keep = lambda df: df[pd.to_datetime(df[mapping.date]).dt.date >= since].reset_index(drop=True)
    return keep(facts), keep(manifest)
