"""DataRanger as an MCP server, so AI agents can patrol data before they trust it.

Run it with ``dataranger mcp`` (or ``uvx dataranger mcp``) and point any MCP
client at that command: Claude Desktop, Claude Code, Cursor, VS Code or your own agent.

The tools are read-only towards your data. Inputs are opened read-only, nothing is written
back, and every output (report, audit log, incidents, proposed fixed version) lands in a
separate folder for a person to review. An agent can learn what is wrong and what DataRanger
could prove; it cannot apply a fix to your warehouse through this server.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

from typing_extensions import TypedDict  # pydantic needs this TypedDict on Python < 3.12

from pydantic import Field

# Parameter documentation lives in the JSON schema, where MCP clients show it to the agent.
OutDir = Annotated[str | None, Field(description=(
    "Folder to write the report files into. Created if missing; report files already there are overwritten. "
    "Omit to use a new temporary folder; its path comes back in outputs.folder."))]
Key = Annotated[str, Field(description="Column in the fact table that uniquely identifies a row (the primary key).")]
Reference = Annotated[str, Field(description="Column in the fact table that points to the reference table (the foreign key).")]
RefKey = Annotated[str, Field(description="Column in the reference table that the fact table's reference column must match.")]
DateCol = Annotated[str, Field(description=(
    "Date column the fact table is partitioned by, one partition per calendar day. "
    "The control totals must have a column with the same name."))]
Segment = Annotated[str, Field(description=(
    "Column that splits each day into segments, for example channel or region. "
    "The control totals must have a column with the same name, where '*' means the whole day."))]
Category = Annotated[str | None, Field(description="Optional categorical column in the fact table. Omit if the table has none.")]
Amount = Annotated[str, Field(description="Numeric amount column in the fact table. It is compared with the 'amount' control total.")]
Threshold = Annotated[float | None, Field(description=(
    "Gate threshold: the largest relative gap to the control totals that a verified fix may leave and still be "
    "marked auto-fixed (0.01 = 1%). Lower is stricter. Omit for the benchmark-calibrated default, 0.0135."))]

Days = Annotated[int | None, Field(description=(
    "Read only the last N days of the fact table and control totals (today counts as day one). "
    "Use at least 30 so the checks have a baseline. Pass null to read the whole table, which can be slow and costly."))]


class Incident(TypedDict):
    partition: str  # YYYY-MM-DD, or "table-level"
    problems: list[str]  # detector names, e.g. duplicate_keys, volume_drop, orphan_keys
    decision: str  # "auto-fixed", "no action needed" or "escalate"
    proposed_fix: str | None
    residual: float | None  # largest relative gap to the control totals after the proposed fix


class Outputs(TypedDict):
    folder: str
    dashboard: str
    audit_log: str
    incidents_csv: str
    proposed_fixed_version: str


class PatrolSummary(TypedDict):
    table: str
    rows_checked: int
    incidents: int
    auto_fixed: int
    no_action_needed: int
    escalate: int
    gate_threshold: float
    needs_a_person: list[Incident]
    all_incidents: list[Incident]
    outputs: Outputs
    note: str

INSTRUCTIONS = (
    "DataRanger checks a date-partitioned fact table (in DuckDB, CSV or Parquet files, Snowflake, or behind a dbt model) against its reference table and the source's "
    "control totals. It finds broken partitions, proposes fixes, verifies each fix on a copy, and only "
    "marks a fix 'auto-fixed' when it is proven and low risk; everything else is 'escalate', meaning a "
    "person must decide. Call a patrol tool before relying on a table, report escalations to the user "
    "rather than working around them, and never claim data was changed: these tools only write a report."
)


def _out_dir(out_dir: str | None) -> Path:
    return Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="dataranger-"))


def _finite(x):
    """JSON has no infinity: a fix that could not be checked against control totals reports no residual."""
    return x if isinstance(x, (int, float)) and x == x and abs(x) != float("inf") else None


def _summary(res, out: Path, table: str) -> PatrolSummary:
    counts = {d: sum(r["decision"] == d for r in res.incidents) for d in ("auto-fixed", "no action needed", "escalate")}
    incidents = []
    for r in res.incidents:
        chosen = r.get("chosen") or {}
        incidents.append({
            "partition": r.get("partition") or "table-level",
            "problems": r.get("detectors") or [],
            "decision": r["decision"],
            "proposed_fix": chosen.get("fix"),
            "residual": _finite(chosen.get("residual")),
        })
    return {
        "table": table,
        "rows_checked": res.rows,
        "incidents": len(res.incidents),
        "auto_fixed": counts["auto-fixed"],
        "no_action_needed": counts["no action needed"],
        "escalate": counts["escalate"],
        "gate_threshold": res.lam,
        "needs_a_person": [i for i in incidents if i["decision"] == "escalate"],
        "all_incidents": incidents,
        "outputs": {
            "folder": str(out),
            "dashboard": str(out / "dashboard.html"),
            "audit_log": str(out / "audit.jsonl"),
            "incidents_csv": str(out / "incidents.csv"),
            "proposed_fixed_version": str(out / "fixed_version.csv"),
        },
        "note": "Input data was not modified. Fixes exist only in the proposed fixed version for review.",
    }


def _run(facts, refs, manifest, table: str, out_dir: str | None, mapping_kwargs: dict, threshold: float | None) -> PatrolSummary:
    from .patrol import DEFAULT_LAMBDA, Mapping, patrol, write_outputs

    mapping = Mapping(**mapping_kwargs)
    try:
        res = patrol(facts, refs, manifest, mapping, lam=DEFAULT_LAMBDA if threshold is None else threshold)
    except SystemExit as e:  # mapping errors are raised as SystemExit for the CLI
        raise ValueError(str(e)) from None
    out = _out_dir(out_dir)
    write_outputs(res, out, f"DataRanger patrol: {table}", table=table)
    return _summary(res, out, table)


def patrol_duckdb(
        db: Annotated[str, Field(description="Path to the DuckDB database file. Opened read-only.")],
        table: Annotated[str, Field(description="Fact table to check, optionally schema-qualified, for example gold.payments.")],
        ref_table: Annotated[str, Field(description="Reference table that fact rows point to, for example customers.")],
        manifest_table: Annotated[str, Field(description=(
            "Table of the source system's control totals, one row per date and segment, with columns: "
            "the date column, the segment column ('*' = whole day), n (row count) and amount (sum)."))],
        out_dir: OutDir = None, key: Key = "txn_id", reference: Reference = "customer_id",
        ref_key: RefKey = "customer_id", date: DateCol = "txn_date", segment: Segment = "channel",
        category: Category = None, amount: Amount = "amount", threshold: Threshold = None) -> PatrolSummary:
    """Check a fact table in a DuckDB database for data-quality incidents and say which are safe to fix.
    A patrol checks every daily partition of the fact table for duplicate keys, duplicate rows, missing
    partitions, rows that point to no reference record, null spikes, and row counts or amounts that do not
    match the source's control totals. For each problem it proposes a fix, tests that fix on a copy against
    the control totals, and decides: "auto-fixed" (proven and low risk), "no action needed" (checked, the
    data is right) or "escalate" (a person must decide).

    Use this before trusting, querying or building on a date-partitioned table that lives in a DuckDB
    database, or after a load or pipeline change to confirm the data is still right. Use patrol_files
    instead when the data is in CSV, TSV or Parquet files. Use run_demo to see the output on sample data.

    Behavior: The database is opened read-only. Your data is never modified and nothing is written back. The only side effect is
    writing four report files to out_dir: dashboard.html, audit.jsonl, incidents.csv, fixed_version.csv
    (the proposed corrected table, for review). Runs locally with no network calls;
    usually a few seconds, longer for tables with millions of rows. Fails with a readable error if a
    file, table or mapped column does not exist; the error lists the columns that are available.

    Returns the counts (incidents, auto_fixed, no_action_needed, escalate), needs_a_person (the escalated
    incidents), all_incidents (partition, problems, decision, proposed_fix, residual for each) and
    outputs (paths to the report files). Pass outputs.folder and a partition to get_incident for the
    full evidence behind one decision. Tell the user about every escalated incident.
    """
    from .patrol import from_duckdb

    facts, refs, manifest = from_duckdb(db, table, ref_table, manifest_table)
    return _run(facts, refs, manifest, table, out_dir,
                dict(key=key, reference=reference, ref_key=ref_key, date=date, segment=segment,
                     category=category, amount=amount), threshold)


def patrol_files(
        facts_file: Annotated[str, Field(description="Path to the fact table as .csv, .tsv, .parquet or .pq. One row per event, partitioned by a date column.")],
        ref_file: Annotated[str, Field(description="Path to the reference table that fact rows point to (for example customers), as .csv, .tsv, .parquet or .pq.")],
        manifest_file: Annotated[str, Field(description=(
            "Path to the source system's control totals as .csv, .tsv, .parquet or .pq: one row per date and "
            "segment, with the date column, the segment column ('*' = whole day), n (row count) and amount (sum)."))],
        out_dir: OutDir = None, key: Key = "txn_id", reference: Reference = "customer_id",
        ref_key: RefKey = "customer_id", date: DateCol = "txn_date", segment: Segment = "channel",
        category: Category = None, amount: Amount = "amount", threshold: Threshold = None) -> PatrolSummary:
    """Check a fact table in CSV, TSV or Parquet files for data-quality incidents and say which are safe to fix.
    A patrol checks every daily partition of the fact table for duplicate keys, duplicate rows, missing
    partitions, rows that point to no reference record, null spikes, and row counts or amounts that do not
    match the source's control totals. For each problem it proposes a fix, tests that fix on a copy against
    the control totals, and decides: "auto-fixed" (proven and low risk), "no action needed" (checked, the
    data is right) or "escalate" (a person must decide).

    Use this before trusting, querying or building on a date-partitioned table exported as CSV, TSV or
    Parquet files, or after a load or pipeline change to confirm the data is still right. Use
    patrol_duckdb instead when the table lives in a DuckDB database. Use run_demo to see the output on
    sample data.

    Behavior: The three input files are only read. Your data is never modified and nothing is written back. The only side effect is
    writing four report files to out_dir: dashboard.html, audit.jsonl, incidents.csv, fixed_version.csv
    (the proposed corrected table, for review). Runs locally with no network calls;
    usually a few seconds, longer for tables with millions of rows. Fails with a readable error if a
    file, table or mapped column does not exist; the error lists the columns that are available.

    Returns the counts (incidents, auto_fixed, no_action_needed, escalate), needs_a_person (the escalated
    incidents), all_incidents (partition, problems, decision, proposed_fix, residual for each) and
    outputs (paths to the report files). Pass outputs.folder and a partition to get_incident for the
    full evidence behind one decision. Tell the user about every escalated incident.
    """
    from .patrol import from_files

    facts, refs, manifest = from_files(facts_file, ref_file, manifest_file)
    return _run(facts, refs, manifest, Path(facts_file).stem, out_dir,
                dict(key=key, reference=reference, ref_key=ref_key, date=date, segment=segment,
                     category=category, amount=amount), threshold)


def patrol_snowflake(
        table: Annotated[str, Field(description="Fact table to check, for example ANALYTICS.GOLD.PAYMENTS. An unqualified name uses the connection's database and schema.")],
        ref_table: Annotated[str, Field(description="Reference table that fact rows point to, for example ANALYTICS.GOLD.CUSTOMERS.")],
        manifest_table: Annotated[str, Field(description=(
            "Table of the source system's control totals, one row per date and segment, with columns: "
            "the date column, the segment column ('*' = whole day), n (row count) and amount (sum)."))],
        connection: Annotated[str | None, Field(description=(
            "Name of a connection in ~/.snowflake/connections.toml. Omit to use the server's SNOWFLAKE_ACCOUNT, "
            "SNOWFLAKE_USER, SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH, SNOWFLAKE_ROLE and SNOWFLAKE_WAREHOUSE "
            "environment variables, or the Snowflake connector's default connection."))] = None,
        days: Days = 30,
        out_dir: OutDir = None, key: Key = "txn_id", reference: Reference = "customer_id",
        ref_key: RefKey = "customer_id", date: DateCol = "txn_date", segment: Segment = "channel",
        category: Category = None, amount: Amount = "amount", threshold: Threshold = None) -> PatrolSummary:
    """Check a fact table in Snowflake for data-quality incidents and say which are safe to fix.

    A patrol checks every daily partition of the fact table for duplicate keys, duplicate rows, missing
    partitions, rows that point to no reference record, null spikes, and row counts or amounts that do not
    match the source's control totals. For each problem it proposes a fix, tests that fix on a copy against
    the control totals, and decides: "auto-fixed" (proven and low risk), "no action needed" (checked, the
    data is right) or "escalate" (a person must decide).

    Use this before trusting, querying or building on a date-partitioned Snowflake table, or after a load to
    confirm the data is still right. Use patrol_dbt instead when the table is built by a dbt model,
    patrol_duckdb for a DuckDB file and patrol_files for CSV or Parquet exports. Use run_demo to see the
    output on sample data.

    Behavior: connects to Snowflake with credentials already configured on this machine; none are passed
    as arguments. It runs three SELECT statements and nothing else, so the warehouse is never modified,
    and it selects only the mapped columns for the last `days` days, so it uses a little warehouse compute
    (usually seconds for 30 days). The only side effect is writing four report files to out_dir:
    dashboard.html, audit.jsonl, incidents.csv and fixed_version.csv (the proposed corrected table, for
    review). Fails with a readable error if the login is refused, or a table or mapped column does not exist.

    Returns the counts (incidents, auto_fixed, no_action_needed, escalate), needs_a_person (the escalated
    incidents), all_incidents (partition, problems, decision, proposed_fix, residual for each) and
    outputs (paths to the report files). Pass outputs.folder and a partition to get_incident for the
    full evidence behind one decision. Tell the user about every escalated incident.
    """
    from .connectors.snowflake import from_snowflake
    from .patrol import Mapping

    columns = dict(key=key, reference=reference, ref_key=ref_key, date=date, segment=segment, category=category, amount=amount)
    facts, refs, manifest = from_snowflake(table, ref_table, manifest_table, Mapping(**columns), days=days, connection=connection)
    return _run(facts, refs, manifest, table, out_dir, columns, threshold)


def patrol_dbt(
        project_dir: Annotated[str, Field(description="Path to the dbt project folder, the one that holds dbt_project.yml. It must have been built or parsed, so target/manifest.json exists.")],
        model: Annotated[str, Field(description="Name of the dbt fact model to check, for example fct_payments. A seed, snapshot, or a source written as source_name.table_name also works.")],
        ref_model: Annotated[str | None, Field(description="dbt model of the reference table that fact rows point to, for example dim_customers. Omit if the fact model's meta.dataranger block sets ref_model.")] = None,
        manifest_model: Annotated[str | None, Field(description=(
            "dbt model holding the source system's control totals: one row per date and segment ('*' = whole day) "
            "with n and amount. Omit if the fact model's meta.dataranger block sets manifest_model."))] = None,
        target: Annotated[str | None, Field(description="dbt target to read from, for example prod. Omit for the profile's default target.")] = None,
        profiles_dir: Annotated[str | None, Field(description="Folder that holds profiles.yml. Omit to look in DBT_PROFILES_DIR, the project folder, then ~/.dbt.")] = None,
        days: Days = 30,
        out_dir: OutDir = None,
        key: Annotated[str | None, Field(description="Primary key column of the fact model. Omit to use meta.dataranger.key, or txn_id.")] = None,
        reference: Annotated[str | None, Field(description="Fact column that points to the reference model. Omit to use meta.dataranger.reference, or customer_id.")] = None,
        ref_key: Annotated[str | None, Field(description="Key column of the reference model. Omit to use meta.dataranger.ref_key, or customer_id.")] = None,
        date: Annotated[str | None, Field(description="Date column the fact model is partitioned by; the control totals need the same column. Omit to use meta.dataranger.date, or txn_date.")] = None,
        segment: Annotated[str | None, Field(description="Column that splits each day into segments, such as channel; the control totals need the same column. Omit to use meta.dataranger.segment, or channel.")] = None,
        category: Annotated[str | None, Field(description="Optional categorical column of the fact model. Omit to use meta.dataranger.category, or none.")] = None,
        amount: Annotated[str | None, Field(description="Numeric amount column of the fact model. Omit to use meta.dataranger.amount, or amount.")] = None,
        threshold: Threshold = None) -> PatrolSummary:
    """Check the table behind a dbt model for data-quality incidents and say which are safe to fix.

    A patrol checks every daily partition of the fact table for duplicate keys, duplicate rows, missing
    partitions, rows that point to no reference record, null spikes, and row counts or amounts that do not
    match the source's control totals. For each problem it proposes a fix, tests that fix on a copy against
    the control totals, and decides: "auto-fixed" (proven and low risk), "no action needed" (checked, the
    data is right) or "escalate" (a person must decide).

    Use this after `dbt build` or `dbt run`, before trusting or publishing a date-partitioned model. It
    finds the model's table from dbt's manifest, connects the way the project's profile says (DuckDB and
    Snowflake targets are supported), and takes the column mapping from the model's `meta: dataranger:`
    block unless you pass columns here. Use patrol_snowflake or patrol_duckdb for tables that are not dbt
    models, and patrol_files for CSV or Parquet exports.

    Behavior: reads target/manifest.json, dbt_project.yml and profiles.yml; it does not run dbt. It then
    runs three SELECT statements and nothing else, so neither the warehouse nor the dbt project is
    modified. On Snowflake it selects only the mapped columns for the last `days` days. The only side
    effect is writing four report files to out_dir: dashboard.html, audit.jsonl, incidents.csv and
    fixed_version.csv (the proposed corrected table, for review). Fails with a readable error if the
    manifest is missing (run dbt first), a model is unknown, the target type is not supported, or a
    mapped column does not exist.

    Returns the counts (incidents, auto_fixed, no_action_needed, escalate), needs_a_person (the escalated
    incidents), all_incidents (partition, problems, decision, proposed_fix, residual for each) and
    outputs (paths to the report files). Pass outputs.folder and a partition to get_incident for the
    full evidence behind one decision. Tell the user about every escalated incident.
    """
    from .connectors.dbt import from_dbt

    overrides = dict(key=key, reference=reference, ref_key=ref_key, date=date, segment=segment, category=category, amount=amount)
    facts, refs, manifest, mapping, name = from_dbt(project_dir, model, mapping_overrides=overrides, ref_model=ref_model,
                                                    manifest_model=manifest_model, target=target,
                                                    profiles_dir=profiles_dir, days=days)
    columns = dict(key=mapping.key, reference=mapping.reference, ref_key=mapping.ref_key, date=mapping.date,
                   segment=mapping.segment, category=mapping.category, amount=mapping.amount)
    return _run(facts, refs, manifest, name, out_dir, columns, threshold)


def get_incident(
        out_dir: Annotated[str, Field(description="The outputs.folder value returned by a patrol tool or run_demo.")],
        partition: Annotated[str, Field(description=(
            "Which incident: the partition date as YYYY-MM-DD, copied from an incident's 'partition' field, "
            "or 'table-level' for a problem with the whole table such as a schema change."))],
) -> dict[str, Any]:
    """Return the full evidence behind one incident from a patrol that has already run.

    Use this after a patrol tool (patrol_duckdb, patrol_files, patrol_snowflake, patrol_dbt) or run_demo, when you need to explain why a partition was
    auto-fixed or escalated, or to show the user what to review. A patrol records at most one incident per
    partition, so the folder plus the partition date identifies it. Do not call it before a patrol.

    Behavior: read-only. It reads audit.jsonl in out_dir and changes nothing; no network calls, returns
    immediately, and the result is one record of a few kilobytes. Fails with a readable error if the
    folder has no audit.jsonl (run a patrol first) or no incident exists for that partition.

    Returns one record with: partition, detectors (the problems found), decision, context (row counts and
    amounts for the day and for each segment, next to the source's control totals), candidates (every fix
    that was tried, with whether it kept the invariants and the residual gap it left) and chosen (the
    best-supported candidate).
    """
    path = Path(out_dir) / "audit.jsonl"
    if not path.exists():
        raise ValueError(f"no audit.jsonl in {out_dir}; run a patrol tool first")
    want = None if partition in ("", "table-level") else partition
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("partition") == want:
                return rec
    raise ValueError(f"no incident for partition {partition!r} in {path}")


def run_demo(
        out_dir: OutDir = None,
        seed: Annotated[int, Field(description="Random seed for the generated data. The same seed on the same day gives the same table.")] = 7,
) -> PatrolSummary:
    """Run a patrol on generated sample data, to see what DataRanger reports without touching real data.

    Generates 60 days of synthetic payment transactions with planted problems (duplicates, missing days,
    orphan rows, wrong amounts and some harmless decoys), then patrols them exactly as patrol_duckdb and
    patrol_files do. Use this to try DataRanger, to show a user the report format, or to test your own
    handling of the results. Use patrol_duckdb or patrol_files for real tables.

    Behavior: needs no input data, credentials or network. The only side effect is writing the report
    files (dashboard.html, audit.jsonl, incidents.csv, fixed_version.csv) to out_dir. Takes a few seconds.

    Returns the same summary as the patrol tools: counts, needs_a_person, all_incidents and outputs.
    Pass outputs.folder and a partition to get_incident for the evidence behind one decision.
    """
    from datetime import date, timedelta

    from .bench import generate, inject
    from .patrol import Mapping, patrol, write_outputs

    case = inject(generate(seed, "small", start=date.today() - timedelta(days=60)))
    res = patrol(case.warehouse["transactions"], case.warehouse["customers"], case.manifest, Mapping(),
                 source_facts=case.source["transactions"])
    out = _out_dir(out_dir)
    write_outputs(res, out, "DataRanger demo patrol", table="demo.payments_daily")
    return _summary(res, out, "demo.payments_daily")


TOOLS = (patrol_duckdb, patrol_files, patrol_snowflake, patrol_dbt, get_incident, run_demo)
# title, read-only (writes nothing at all), open world (reaches a system outside this machine)
TOOL_HINTS = {
    "patrol_duckdb": ("Patrol a DuckDB table", False, False),
    "patrol_files": ("Patrol CSV or Parquet files", False, False),
    "patrol_snowflake": ("Patrol a Snowflake table", False, True),
    "patrol_dbt": ("Patrol a dbt model", False, True),
    "get_incident": ("Get incident evidence", True, False),
    "run_demo": ("Run the demo patrol", False, False),
}


def build_server():
    """Create the MCP server. Works with the MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer)."""
    import functools

    try:
        from mcp.server.mcpserver import MCPServer as Server
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server
            from mcp.server.fastmcp.exceptions import ToolError
        except ImportError:
            raise SystemExit("The MCP server needs the mcp package: pip install mcp") from None

    def visible_errors(fn):
        # Input problems (wrong column, missing file) go back to the agent as a readable message.
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ValueError, FileNotFoundError) as e:
                raise ToolError(str(e)) from None
            except Exception as e:  # duckdb and pandas errors such as a missing table
                raise ToolError(f"{type(e).__name__}: {e}") from None
        return wrapper

    from mcp.types import ToolAnnotations

    def hints(title: str, read_only: bool, open_world: bool) -> ToolAnnotations:
        # Patrols write report files to a local folder, so only get_incident is strictly read-only.
        # A patrol without out_dir makes a new temporary folder each time, so it is not idempotent either.
        return ToolAnnotations.model_validate({"title": title, "readOnlyHint": read_only, "destructiveHint": False,
                                               "idempotentHint": read_only, "openWorldHint": open_world})

    server = Server(name="dataranger", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool(annotations=hints(*TOOL_HINTS[fn.__name__]))(visible_errors(fn))
    return server


def main() -> None:
    build_server().run()
