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

INSTRUCTIONS = (
    "DataRanger checks a date-partitioned fact table against its reference table and the source's "
    "control totals. It finds broken partitions, proposes fixes, verifies each fix on a copy, and only "
    "marks a fix 'auto-fixed' when it is proven and low risk; everything else is 'escalate', meaning a "
    "person must decide. Call a patrol tool before relying on a table, report escalations to the user "
    "rather than working around them, and never claim data was changed: these tools only write a report."
)


def _out_dir(out_dir: str | None) -> Path:
    return Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="dataranger-"))


def _summary(res, out: Path, table: str) -> dict:
    counts = {d: sum(r["decision"] == d for r in res.incidents) for d in ("auto-fixed", "no action needed", "escalate")}
    incidents = []
    for r in res.incidents:
        chosen = r.get("chosen") or {}
        incidents.append({
            "partition": r.get("partition") or "table-level",
            "problems": r.get("detectors") or [],
            "decision": r["decision"],
            "proposed_fix": chosen.get("fix"),
            "residual": chosen.get("residual"),
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


def _run(facts, refs, manifest, table: str, out_dir: str | None, mapping_kwargs: dict, threshold: float | None) -> dict:
    from .patrol import DEFAULT_LAMBDA, Mapping, patrol, write_outputs

    mapping = Mapping(**mapping_kwargs)
    try:
        res = patrol(facts, refs, manifest, mapping, lam=DEFAULT_LAMBDA if threshold is None else threshold)
    except SystemExit as e:  # mapping errors are raised as SystemExit for the CLI
        raise ValueError(str(e)) from None
    out = _out_dir(out_dir)
    write_outputs(res, out, f"DataRanger patrol: {table}", table=table)
    return _summary(res, out, table)


def patrol_duckdb(db: str, table: str, ref_table: str, manifest_table: str, out_dir: str | None = None,
                  key: str = "txn_id", reference: str = "customer_id", ref_key: str = "customer_id",
                  date: str = "txn_date", segment: str = "channel", category: str | None = None,
                  amount: str = "amount", threshold: float | None = None) -> dict:
    """Patrol a fact table in a DuckDB file (opened read-only).

    Args:
        db: path to the DuckDB database file.
        table: fact table, optionally schema-qualified (e.g. gold.payments).
        ref_table: reference table that fact rows point to (e.g. customers).
        manifest_table: the source's control totals: date, segment ('*' for the day total), n, amount.
        out_dir: folder for the report and audit log; a temporary folder if omitted.
        key, reference, ref_key, date, segment, category, amount: your column names.
        threshold: gate threshold; leave empty for the benchmark-calibrated default.
    """
    from .patrol import from_duckdb

    facts, refs, manifest = from_duckdb(db, table, ref_table, manifest_table)
    return _run(facts, refs, manifest, table, out_dir,
                dict(key=key, reference=reference, ref_key=ref_key, date=date, segment=segment,
                     category=category, amount=amount), threshold)


def patrol_files(facts_file: str, ref_file: str, manifest_file: str, out_dir: str | None = None,
                 key: str = "txn_id", reference: str = "customer_id", ref_key: str = "customer_id",
                 date: str = "txn_date", segment: str = "channel", category: str | None = None,
                 amount: str = "amount", threshold: float | None = None) -> dict:
    """Patrol exported CSV, TSV or Parquet files (read only).

    Args:
        facts_file: the date-partitioned fact table.
        ref_file: reference table that fact rows point to.
        manifest_file: the source's control totals: date, segment ('*' for the day total), n, amount.
        out_dir: folder for the report and audit log; a temporary folder if omitted.
        key, reference, ref_key, date, segment, category, amount: your column names.
        threshold: gate threshold; leave empty for the benchmark-calibrated default.
    """
    from .patrol import from_files

    facts, refs, manifest = from_files(facts_file, ref_file, manifest_file)
    return _run(facts, refs, manifest, Path(facts_file).stem, out_dir,
                dict(key=key, reference=reference, ref_key=ref_key, date=date, segment=segment,
                     category=category, amount=amount), threshold)


def get_incident(out_dir: str, partition: str) -> dict:
    """Return the full evidence for one incident from a previous patrol.

    Args:
        out_dir: the outputs folder returned by a patrol tool.
        partition: the partition date (YYYY-MM-DD), or 'table-level'.
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


def run_demo(out_dir: str | None = None, seed: int = 7) -> dict:
    """Patrol a generated payments table with planted problems, to see what DataRanger reports.

    Args:
        out_dir: folder for the report and audit log; a temporary folder if omitted.
        seed: random seed for the generated data.
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


TOOLS = (patrol_duckdb, patrol_files, get_incident, run_demo)


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

    server = Server(name="dataranger", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool()(visible_errors(fn))
    return server


def main() -> None:
    build_server().run()
