"""Read-only metadata completeness check (issue #88).

Suggested in feedback on the DataRanger launch post: connect DataRanger to the warehouse's own
metadata so it can flag columns and tables with no business definition. This module answers one
question only -- is this column or table documented? -- from whichever source has that answer:
dbt's own manifest (`description` and `meta`), Snowflake's INFORMATION_SCHEMA (`COMMENT`), or
DuckDB's catalog comments (`COMMENT ON ...`). Nothing is auto-fixed and no definition is written;
these are findings for a person, reported alongside a patrol.

Out of scope for now (per the issue): writing definitions, and connecting to external catalogs
(a business glossary, Collibra, Atlan, and the like).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableMeta:
    table: str
    description: str | None
    owner: str | None
    columns: dict[str, str | None]  # column name -> description, or None if undocumented

    @property
    def undocumented_columns(self) -> list[str]:
        return [c for c, d in self.columns.items() if not d]

    @property
    def columns_total(self) -> int:
        return len(self.columns)

    @property
    def columns_documented(self) -> int:
        return self.columns_total - len(self.undocumented_columns)


@dataclass
class MetadataReport:
    tables: list[TableMeta] = field(default_factory=list)

    @property
    def columns_total(self) -> int:
        return sum(t.columns_total for t in self.tables)

    @property
    def columns_documented(self) -> int:
        return sum(t.columns_documented for t in self.tables)

    @property
    def tables_without_description(self) -> list[str]:
        return [t.table for t in self.tables if not t.description]

    @property
    def tables_without_owner(self) -> list[str]:
        return [t.table for t in self.tables if not t.owner]

    def coverage_line(self) -> str:
        """The one-line coverage summary for the summary and JSON output, e.g. "definitions: 14 of 19 columns"."""
        return f"definitions: {self.columns_documented} of {self.columns_total} columns"

    def to_rows(self) -> list[dict]:
        """One row per table, for a reporting sheet."""
        return [{"table": t.table, "description": t.description or "", "owner": t.owner or "",
                 "columns_total": t.columns_total, "columns_documented": t.columns_documented,
                 "undocumented_columns": "; ".join(t.undocumented_columns)} for t in self.tables]

    def to_dict(self) -> dict:
        """JSON-friendly summary: the coverage line, the gaps, and the detail behind them."""
        return {
            "coverage": self.coverage_line(),
            "columns_total": self.columns_total,
            "columns_documented": self.columns_documented,
            "tables_without_description": self.tables_without_description,
            "tables_without_owner": self.tables_without_owner,
            "tables": [{"table": t.table, "description": t.description, "owner": t.owner,
                       "columns_total": t.columns_total, "columns_documented": t.columns_documented,
                       "undocumented_columns": t.undocumented_columns} for t in self.tables],
        }


def from_dbt(nodes: list[dict]) -> MetadataReport:
    """Build a report from dbt's own manifest nodes: `description` on the model and its columns,
    and `meta.owner` for who owns it. Pass the fact model's node and its reference model's node
    (``dbt.find_node(manifest, name)`` finds either)."""
    tables = []
    for node in nodes:
        meta = {**(node.get("config", {}).get("meta") or {}), **(node.get("meta") or {})}
        owner = meta.get("owner") or None
        cols = {name: (col.get("description") or None) for name, col in (node.get("columns") or {}).items()}
        tables.append(TableMeta(table=node.get("name", "?"), description=node.get("description") or None,
                                owner=owner, columns=cols))
    return MetadataReport(tables)


def _table_parts(name: str) -> tuple[str | None, str]:
    """(schema, table) from a possibly schema-qualified name; schema is None when unqualified."""
    parts = name.split(".")
    return (parts[-2] if len(parts) > 1 else None), parts[-1]


def from_duckdb(db: str, tables: list[str]) -> MetadataReport:
    """Build a report by reading comments from a DuckDB database's own catalog (``COMMENT ON ...``)."""
    import duckdb

    con = duckdb.connect(db, read_only=True)
    try:
        out = []
        for t in tables:
            schema, name = _table_parts(t)
            if schema:
                trow = con.execute(
                    "select comment from duckdb_tables() where table_name = ? and schema_name = ?", [name, schema]
                ).fetchone()
                crows = con.execute(
                    "select column_name, comment from duckdb_columns() where table_name = ? and schema_name = ? "
                    "order by column_index", [name, schema]).fetchall()
            else:
                trow = con.execute("select comment from duckdb_tables() where table_name = ?", [name]).fetchone()
                crows = con.execute(
                    "select column_name, comment from duckdb_columns() where table_name = ? order by column_index",
                    [name]).fetchall()
            out.append(TableMeta(table=t, description=(trow[0] if trow else None), owner=None,
                                 columns={c: d for c, d in crows}))
        return MetadataReport(out)
    finally:
        con.close()


def _sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def from_snowflake(tables: list[str], connection: str | None = None, params: dict | None = None, opener=None) -> MetadataReport:
    """Build a report by reading `COMMENT` from Snowflake's INFORMATION_SCHEMA. Read-only: two SELECTs per table."""
    from .connectors import snowflake as sf

    out = []
    for t in tables:
        schema, name = _table_parts(t)
        where = f"upper(table_name) = upper({_sql_literal(name)})"
        if schema:
            where += f" and upper(table_schema) = upper({_sql_literal(schema)})"
        tquery = f"select comment from information_schema.tables where {where}"
        cquery = f"select column_name, comment from information_schema.columns where {where} order by ordinal_position"
        tframe, cframe = sf.run_queries([tquery, cquery], connection, params, opener)
        description = None
        if len(tframe) and "COMMENT" in tframe.columns or "comment" in getattr(tframe, "columns", []):
            col = "COMMENT" if "COMMENT" in tframe.columns else "comment"
            vals = tframe[col].dropna()
            description = str(vals.iloc[0]) if len(vals) else None
        cols: dict[str, str | None] = {}
        if len(cframe):
            name_col = "COLUMN_NAME" if "COLUMN_NAME" in cframe.columns else "column_name"
            comment_col = "COMMENT" if "COMMENT" in cframe.columns else "comment"
            for _, row in cframe.iterrows():
                val = row[comment_col]
                cols[str(row[name_col])] = (str(val) if val not in (None, "") else None)
        out.append(TableMeta(table=t, description=description, owner=None, columns=cols))
    return MetadataReport(out)
