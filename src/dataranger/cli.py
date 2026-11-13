"""DataRanger command line: demo, patrol, calibrate, bench, plan and mcp."""
import argparse

from . import __version__

STAGES = [
    ("Detect", "rules and statistics find the problem; no LLM"),
    ("Diagnose", "an LLM answers one narrow question about the cause"),
    ("Propose fix", "a templated fix; the LLM fills parameters only"),
    ("Verify", "pipeverify checks the fix against source data, on a copy"),
    ("Gate", "apply as a new version if proven and low risk; otherwise escalate"),
    ("Report", "every decision lands on the dashboard and the audit log"),
]


def print_summary(res, out, json_output=False):
    n = {d: sum(r["decision"] == d for r in res.incidents) for d in ("auto-fixed", "no action needed", "escalate")}
    if json_output:
        import json
        from pathlib import Path

        print(json.dumps({"incidents": len(res.incidents), "auto_fixed": n["auto-fixed"],
                          "no_action": n["no action needed"], "escalate": n["escalate"],
                          "report": str(Path(out) / "dashboard.html")}))
        return
    print(f"{len(res.incidents)} incidents: {n['auto-fixed']} fixed, {n['no action needed']} checked and left alone, "
          f"{n['escalate']} for you. Report: {out}/dashboard.html")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dataranger", description=__doc__)
    parser.add_argument("--version", action="version", version=f"dataranger {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("plan", help="show the patrol loop DataRanger will run")
    sub.add_parser("mcp", help="run DataRanger as an MCP server for AI agents (stdio)")
    b = sub.add_parser("bench", help="generate a DQ-Repair case and run the detectors on it")
    b.add_argument("--size", choices=["small", "medium", "large"], default="small")
    b.add_argument("--seed", type=int, default=1)
    dm = sub.add_parser("demo", help="patrol a generated table with planted problems and write the report")
    dm.add_argument("--out", default="dataranger-demo")
    dm.add_argument("--seed", type=int, default=7)
    pt = sub.add_parser("patrol", help="patrol your own tables: DuckDB, CSV/Parquet files, Snowflake or dbt models")
    src = pt.add_argument_group("DuckDB or Snowflake source")
    src.add_argument("--db", help="DuckDB database file")
    src.add_argument("--snowflake", nargs="?", const="", metavar="CONNECTION",
                     help="read from Snowflake; optionally name a connection from ~/.snowflake/connections.toml, "
                          "otherwise SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and friends are used")
    src.add_argument("--table", help="facts table (default report title when files are used)")
    src.add_argument("--ref-table", help="reference (customers) table")
    src.add_argument("--manifest-table", help="control totals: date, segment ('*' = total), n, amount")
    files = pt.add_argument_group("file source")
    files.add_argument("--facts-file", help="facts as .csv, .tsv or .parquet")
    files.add_argument("--ref-file", help="reference (customers) as .csv, .tsv or .parquet")
    files.add_argument("--manifest-file", help="control totals as .csv, .tsv or .parquet")
    dbt = pt.add_argument_group("dbt source")
    dbt.add_argument("--dbt-model", help="dbt fact model to patrol; column mapping can live in its meta.dataranger block")
    dbt.add_argument("--dbt-project-dir", default=".", help="folder with dbt_project.yml (default: current folder)")
    dbt.add_argument("--dbt-ref-model", help="dbt model of the reference table (or meta.dataranger.ref_model)")
    dbt.add_argument("--dbt-manifest-model", help="dbt model of the control totals (or meta.dataranger.manifest_model)")
    dbt.add_argument("--dbt-target", help="dbt target to read from (default: the profile's default)")
    dbt.add_argument("--dbt-profiles-dir", help="folder with profiles.yml (default: DBT_PROFILES_DIR, the project, ~/.dbt)")
    pt.add_argument("--key", default=None, help="primary key column of the facts (default: txn_id)")
    pt.add_argument("--reference", default=None, help="facts column that points to the reference table (default: customer_id)")
    pt.add_argument("--ref-key", default=None, help="key column of the reference table (default: customer_id)")
    pt.add_argument("--date", default=None, help="partition date column (default: txn_date)")
    pt.add_argument("--segment", default=None, help="segment column, such as channel (default: channel)")
    pt.add_argument("--category", default=None, help="optional categorical column")
    pt.add_argument("--amount", default=None, help="amount column (default: amount)")
    pt.add_argument("--days", type=int, default=None, help="patrol only the last N days (recommended for warehouses)")
    pt.add_argument("--threshold", type=float, default=None, help="gate threshold (default: benchmark-calibrated)")
    pt.add_argument("--out", default="dataranger-report")
    pt.add_argument("--json", action="store_true", help="print only a machine-readable JSON summary")
    pt.add_argument("--summary-md", metavar="PATH", help="also append a Markdown summary to this file (for CI job summaries)")
    pt.add_argument("--fail-on-escalate", action="store_true", help="exit with code 2 if any incident needs review")
    cb = sub.add_parser("calibrate", help="choose the gate threshold from your own reviewed incidents")
    cb.add_argument("--audit", nargs="+", required=True, help="audit.jsonl files from past patrols")
    cb.add_argument("--labels", required=True, help="CSV with partition, verdict (right or wrong): was the chosen fix right?")
    cb.add_argument("--alpha", type=float, default=0.01)
    cb.add_argument("--delta", type=float, default=0.05)
    args = parser.parse_args(argv)
    if args.command == "calibrate":
        import json
        import pandas as pd
        from .repair.gate import calibrate
        lab = pd.read_csv(args.labels, dtype=str, keep_default_na=False)
        missing = [col for col in ("partition", "verdict") if col not in lab.columns]
        if missing:
            raise SystemExit(f"{args.labels}: missing column(s) {', '.join(missing)}; "
                             "the labels CSV needs partition and verdict (right or wrong).")
        verdicts = lab["verdict"].str.strip().str.lower()
        bad = verdicts[~verdicts.isin(["right", "wrong"])]
        if len(bad):
            # Row numbers count the header as row 1, as a spreadsheet shows them.
            shown = ", ".join(f"row {i + 2}: {lab['verdict'][i]!r}" for i in bad.index[:5])
            more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
            raise SystemExit(f"{args.labels}: verdict must be right or wrong, got {shown}{more}.")
        verdict = dict(zip(lab["partition"].str.strip(), verdicts.eq("right")))
        recs = []
        for f in args.audit:
            with open(f, encoding="utf-8") as audit:
                for line in audit:
                    r = json.loads(line)
                    c = r.get("chosen")
                    if r["table_level"] or not c or str(r["partition"]) not in verdict:
                        continue
                    c["correct"] = verdict[str(r["partition"])]
                    recs.append({"table_level": False, "candidates": [c]})
        lam = calibrate(recs, args.alpha, args.delta)
        if lam < 0:
            print(f"{len(recs)} labeled incidents are not enough to certify alpha={args.alpha}; keep escalating.")
        else:
            print(f"threshold {lam:.4f} keeps wrong auto-fixes at or below {args.alpha:.1%} "
                  f"with {1 - args.delta:.0%} confidence ({len(recs)} labeled incidents). Use --threshold {lam:.4f}.")
        return 0
    if args.command == "demo":
        from pathlib import Path
        from .bench import generate, inject
        from .patrol import Mapping, patrol, write_outputs
        from datetime import date, timedelta
        case = inject(generate(args.seed, "small", start=date.today() - timedelta(days=60)))
        res = patrol(case.warehouse["transactions"], case.warehouse["customers"],
                     case.manifest.rename(columns={"channel": "channel"}), Mapping(),
                     source_facts=case.source["transactions"])
        write_outputs(res, Path(args.out), "DataRanger demo patrol", table="demo.payments_daily")
        print_summary(res, args.out)
        return 0
    if args.command == "patrol":
        from pathlib import Path
        from .connectors import sql
        from .patrol import DEFAULT_LAMBDA, Mapping, from_duckdb, from_files, patrol, write_outputs
        table_given = [args.table, args.ref_table, args.manifest_table]
        file_given = [args.facts_file, args.ref_file, args.manifest_file]
        chosen = [name for name, on in (("--db", bool(args.db)), ("--snowflake", args.snowflake is not None),
                                        ("--dbt-model", bool(args.dbt_model)),
                                        ("--facts-file/--ref-file/--manifest-file", any(file_given))) if on]
        if len(chosen) > 1:
            parser.error(f"patrol: use one source, not both: {' and '.join(chosen)}")
        if not chosen:
            parser.error("patrol: give --db or --snowflake with --table/--ref-table/--manifest-table, "
                         "or --facts-file/--ref-file/--manifest-file, or --dbt-model")
        given = {"key": args.key, "reference": args.reference, "date": args.date, "segment": args.segment,
                 "category": args.category, "amount": args.amount, "ref_key": args.ref_key}
        mp = Mapping(**{"category": None, **{k: v for k, v in given.items() if v is not None}})
        try:
            if args.dbt_model:
                from .connectors.dbt import from_dbt
                facts, refs, man, mp, table = from_dbt(
                    args.dbt_project_dir, args.dbt_model, mapping_overrides=given, ref_model=args.dbt_ref_model,
                    manifest_model=args.dbt_manifest_model, target=args.dbt_target,
                    profiles_dir=args.dbt_profiles_dir, days=args.days)
            elif args.snowflake is not None or args.db:
                source = "--db" if args.db else "--snowflake"
                missing = [n for n, v in zip(("--table", "--ref-table", "--manifest-table"), table_given) if not v]
                if missing:
                    parser.error(f"patrol: missing {', '.join(missing)} for the {source} source")
                table = args.table
                if args.db:
                    facts, refs, man = from_duckdb(args.db, args.table, args.ref_table, args.manifest_table)
                    facts, man = sql.filter_days(facts, man, mp, args.days)
                else:
                    from .connectors.snowflake import from_snowflake
                    facts, refs, man = from_snowflake(args.table, args.ref_table, args.manifest_table, mp,
                                                      days=args.days, connection=args.snowflake or None)
            else:
                missing = [n for n, v in zip(("--facts-file", "--ref-file", "--manifest-file"), file_given) if not v]
                if missing:
                    parser.error(f"patrol: missing {', '.join(missing)} for the file source")
                facts, refs, man = from_files(args.facts_file, args.ref_file, args.manifest_file)
                facts, man = sql.filter_days(facts, man, mp, args.days)
                table = args.table or Path(args.facts_file).stem
        except ValueError as e:
            raise SystemExit(f"patrol: {e}") from None
        if facts is not None and not len(facts):
            raise SystemExit("patrol: the facts table has no rows" + (f" in the last {args.days} days" if args.days else ""))
        res = patrol(facts, refs, man, mp, lam=args.threshold if args.threshold is not None else DEFAULT_LAMBDA)
        write_outputs(res, Path(args.out), f"DataRanger patrol: {table}", table=table)
        if args.summary_md:
            from .summary import markdown
            with open(args.summary_md, "a", encoding="utf-8") as f:
                f.write(markdown(res, table, report=str(Path(args.out) / "dashboard.html")))
        print_summary(res, args.out, json_output=args.json)
        return 2 if args.fail_on_escalate and any(r["decision"] == "escalate" for r in res.incidents) else 0
    if args.command == "bench":
        from .bench import detect, generate, inject, score
        inst = generate(args.seed, args.size)
        case = inject(inst)
        s = score(detect(case.warehouse, inst.days), case.issues)
        for r in s["issues"]:
            status = ("flagged (decoy: leave alone)" if r["flagged"] else "not flagged") if r["decoy"] else \
                ("caught" if r["caught"] else "missed")
            print(f"{r['issue']} {r['kind']:<15} {status}")
        print(f"false alarms on clean partitions: {len(s['false_alarms'])}")
        return 0
    if args.command == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0
    if args.command == "plan":
        for i, (name, what) in enumerate(STAGES, 1):
            print(f"{i}. {name}: {what}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
