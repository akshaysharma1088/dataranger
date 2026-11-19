"""Build control totals from the raw or landing table a fact table is loaded from.

`patrol` checks fixes against the source's control totals: one row per date and segment, plus a row
with segment ``*`` for the whole day, each with ``n`` (rows) and ``amount`` (sum). `totals` writes that
layout from the source table itself, for teams that don't keep such a table yet.
"""
from pathlib import Path

import duckdb
import pandas as pd

from .patrol import _quoted

TOTAL = "*"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _relation(con: duckdb.DuckDBPyConnection, table: str | None, path: str | None) -> str:
    if table:
        return _quoted(table)
    suffix = Path(path).suffix.lower()
    if suffix in (".csv", ".tsv"):
        return f"read_csv_auto({_literal(path)})"
    if suffix in (".parquet", ".pq"):
        return f"read_parquet({_literal(path)})"
    raise ValueError(f"unsupported file type for {path!r}: expected .csv, .tsv, .parquet or .pq")


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def control_totals(con: duckdb.DuckDBPyConnection, relation: str, date: str, segment: str, amount: str) -> pd.DataFrame:
    """Per-day (segment ``*``) and per-segment rows of ``n`` and ``amount`` for *relation*.

    The date column is cast to DATE, so a timestamp counts toward its calendar day. Rows with no date
    can't be placed on a day and raise ValueError. Rows with no segment count toward the day's ``*`` row
    only, because there is no segment to put them under.
    """
    columns = con.execute(f"select * from {relation} limit 0").df().columns.tolist()
    missing = [f"{name!r} ({flag})" for name, flag in ((date, "--date"), (segment, "--segment"), (amount, "--amount"))
               if name not in columns]
    if missing:
        raise ValueError(f"the source table has no column {', '.join(missing)}; columns are: {', '.join(map(str, columns))}")
    d, s, a = _ident(date), _ident(segment), _ident(amount)
    undated = con.execute(f"select count(*) from {relation} where {d} is null").fetchone()[0]
    if undated:
        raise ValueError(f"{undated} row(s) have no {date}, so they can't be counted toward a day")
    star = con.execute(f"select count(*) from {relation} where cast({s} as varchar) = ?", [TOTAL]).fetchone()[0]
    if star:
        raise ValueError(f"{star} row(s) have {segment} {TOTAL!r}, which the control totals use for the whole day")
    out = con.execute(f"""
        select cast({d} as date) as {d},
               case when grouping({s}) = 1 then '{TOTAL}' else cast({s} as varchar) end as {s},
               count(*) as n,
               sum({a}) as amount
        from {relation}
        group by grouping sets ((cast({d} as date)), (cast({d} as date), {s}))
        having grouping({s}) = 1 or {s} is not null
        order by 1, grouping({s}) desc, 2
    """).df()
    # pandas reads DATE as a timestamp; keep calendar dates so the written totals stay DATE / YYYY-MM-DD.
    out[date] = pd.to_datetime(out[date]).dt.date
    return out


def build(*, db: str | None = None, table: str | None = None, facts_file: str | None = None,
          date: str = "txn_date", segment: str = "channel", amount: str = "amount") -> pd.DataFrame:
    """Read the source table (a DuckDB table or a CSV/Parquet file) and return its control totals."""
    con = duckdb.connect(db, read_only=True) if db else duckdb.connect()
    try:
        return control_totals(con, _relation(con, table if db else None, facts_file), date, segment, amount)
    finally:
        con.close()


def _table_key(con: duckdb.DuckDBPyConnection, name: str) -> tuple[str, ...]:
    """*name* as (database, schema, table), lowercased, filling in the current database and schema."""
    parts = [part.lower() for part in name.split(".")]
    if len(parts) < 3:
        database, schema = con.execute("select current_database(), current_schema()").fetchone()
        parts = [database.lower(), schema.lower()][: 3 - len(parts)] + parts
    return tuple(parts)


def write_table(db: str, out_table: str, totals: pd.DataFrame, source_table: str | None = None) -> None:
    """Create or replace *out_table* in the DuckDB database with *totals*.

    An existing table is replaced only if it has the control-totals columns, so a mistyped name can't
    overwrite a fact table. *out_table* may never be *source_table*, even when the source happens to have
    the same columns as the totals.
    """
    con = duckdb.connect(db)
    try:
        if source_table and _table_key(con, out_table) == _table_key(con, source_table):
            raise ValueError(f"{out_table} is the --table the totals are read from; choose another --out-table")
        try:
            existing = con.execute(f"select * from {_quoted(out_table)} limit 0").df().columns.tolist()
        except duckdb.CatalogException:
            existing = None
        if existing is not None and existing != totals.columns.tolist():
            raise ValueError(f"{out_table} already exists and isn't a control-totals table (columns: "
                             f"{', '.join(map(str, existing))}); choose another --out-table")
        parts = out_table.split(".")
        if len(parts) > 1:
            con.execute(f"create schema if not exists {_quoted('.'.join(parts[:-1]))}")
        con.register("dataranger_totals", totals)
        con.execute(f"create or replace table {_quoted(out_table)} as select * from dataranger_totals")
    finally:
        con.close()


OUTPUT_FORMATS = {".csv": "(format csv, header true)", ".tsv": "(format csv, header true, delimiter '\t')",
                  ".parquet": "(format parquet)", ".pq": "(format parquet)"}


def check_output_path(path: str) -> None:
    """Raise ValueError unless *path* ends in an extension `write_file` can write."""
    if Path(path).suffix.lower() not in OUTPUT_FORMATS:
        raise ValueError(f"unsupported output type for {path!r}: expected .csv, .tsv, .parquet or .pq")


def write_file(path: str, totals: pd.DataFrame) -> None:
    """Write *totals* as CSV, TSV or Parquet, following the extension."""
    check_output_path(path)
    con = duckdb.connect()
    try:
        con.register("dataranger_totals", totals)
        con.execute(f"copy dataranger_totals to {_literal(path)} {OUTPUT_FORMATS[Path(path).suffix.lower()]}")
    finally:
        con.close()
