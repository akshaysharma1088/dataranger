"""Build control totals from the raw or landing table a fact table is loaded from, or read them
straight from the source's own trailer or control record.

`patrol` checks fixes against the source's control totals: one row per date and segment, plus a row
with segment ``*`` for the whole day, each with ``n`` (rows) and ``amount`` (sum). `totals` writes that
layout from the source table itself, for teams that don't keep such a table yet (issue #62); or, for
upstream flat files, from the delivery's own trailer record or a sidecar control file, which is
stronger evidence because the sender wrote it (issue #86).
"""
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

import duckdb
import pandas as pd

from .patrol import _quoted

TOTAL = "*"


@dataclass
class TrailerFormat:
    """How to read one control/trailer record.

    A field is either a plain 0-based index into the line's delimiter-split parts (delimited), or a
    ``(start, end)`` 0-based character slice of the raw line (fixed-width); mixing the two in one
    format is not supported. ``record_type_field``/``record_type_value`` pick out the trailer line
    among a delivery file's data rows (for example the last field reading ``"TRL"``); leave
    `record_type_field` as None to read the file's (or sidecar's) last non-blank line as the trailer.
    """

    count_field: int | tuple[int, int]
    amount_field: int | tuple[int, int]
    delimiter: str = ","
    record_type_field: int | tuple[int, int] | None = None
    record_type_value: str | None = None

    @property
    def fixed_width(self) -> bool:
        return isinstance(self.count_field, tuple) or isinstance(self.amount_field, tuple) or \
            isinstance(self.record_type_field, tuple)


def _parse_field_spec(spec: str) -> int | tuple[int, int]:
    """"3" -> 3 (a delimited field index); "10:20" -> (10, 20) (a fixed-width character slice)."""
    if ":" in spec:
        start, end = spec.split(":", 1)
        return int(start), int(end)
    return int(spec)


def _field(line: str, parts: list[str] | None, spec: int | tuple[int, int]) -> str:
    if isinstance(spec, tuple):
        start, end = spec
        return line[start:end].strip()
    return parts[spec].strip()


def read_trailer(path: str, fmt: TrailerFormat, encoding: str = "utf-8") -> tuple[int, float]:
    """Read one delivery file's trailer (or a sidecar control file) and return (n, amount).

    Raises ValueError if the file has no lines, or no line matches `record_type_value` when one is
    given, or the count/amount fields do not parse as a whole number and a number.
    """
    lines = [ln for ln in Path(path).read_text(encoding=encoding).splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"{path} has no lines to read a trailer from")
    parts_of = (lambda ln: None) if fmt.fixed_width else (lambda ln: ln.split(fmt.delimiter))
    if fmt.record_type_field is not None:
        matches = [ln for ln in lines if _field(ln, parts_of(ln), fmt.record_type_field) == fmt.record_type_value]
        if not matches:
            raise ValueError(f"{path} has no line with field {fmt.record_type_field} = {fmt.record_type_value!r}")
        line = matches[-1]
    else:
        line = lines[-1]
    parts = parts_of(line)
    try:
        n = int(_field(line, parts, fmt.count_field))
    except (ValueError, IndexError) as e:
        raise ValueError(f"{path}: could not read the record count from field {fmt.count_field}: {e}") from None
    try:
        amount = float(_field(line, parts, fmt.amount_field))
    except (ValueError, IndexError) as e:
        raise ValueError(f"{path}: could not read the record amount from field {fmt.amount_field}: {e}") from None
    return n, amount


def control_totals_from_trailer(path: str, fmt: TrailerFormat, date: "_date | str", segment: str = TOTAL,
                                encoding: str = "utf-8") -> pd.DataFrame:
    """One control-total row (date, segment, n, amount) read from a delivery file's own trailer.

    `date` is the business date the file covers (the trailer itself rarely says, so the caller
    supplies it); `segment` defaults to the whole-day row ``'*'``, since a single trailer total
    usually covers a whole delivery rather than one segment.
    """
    n, amount = read_trailer(path, fmt, encoding)
    return pd.DataFrame([{"txn_date": pd.Timestamp(date).date(), "channel": segment, "n": n, "amount": float(amount)}])


def read_totals_file(path: str) -> pd.DataFrame:
    """Read a control-totals file written by `totals` (CSV, TSV or Parquet), for --compare-to."""
    con = duckdb.connect()
    try:
        suffix = Path(path).suffix.lower()
        if suffix in (".csv", ".tsv"):
            df = con.execute("select * from read_csv_auto(?)", [path]).df()
        elif suffix in (".parquet", ".pq"):
            df = con.execute("select * from read_parquet(?)", [path]).df()
        else:
            raise ValueError(f"unsupported file type for {path!r}: expected .csv, .tsv, .parquet or .pq")
    finally:
        con.close()
    df["txn_date"] = pd.to_datetime(df["txn_date"]).dt.date
    return df


def compare_trailer_to_computed(trailer: pd.DataFrame, computed: pd.DataFrame) -> pd.DataFrame:
    """Rows where the trailer's totals and the totals computed from the raw table disagree.

    A disagreement here is an incident in its own right (issue #86): the sender's own trailer and
    DataRanger's own count of the landed rows don't match, independently of anything patrol checks
    against the warehouse. Returns an empty frame when every matched (date, segment) agrees.
    """
    merged = trailer.merge(computed, on=["txn_date", "channel"], how="outer", suffixes=("_trailer", "_computed"),
                           indicator=True)
    mismatched = merged[merged["_merge"] != "both"]
    both = merged[merged["_merge"] == "both"]
    disagree = both[(both["n_trailer"] != both["n_computed"]) | (both["amount_trailer"] != both["amount_computed"])]
    out = pd.concat([mismatched, disagree], ignore_index=True).drop(columns=["_merge"])
    return out.sort_values(["txn_date", "channel"], ignore_index=True)


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
