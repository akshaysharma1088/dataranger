"""DataRanger command line: demo, patrol, calibrate, bench and plan."""
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dataranger", description=__doc__)
    parser.add_argument("--version", action="version", version=f"dataranger {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("plan", help="show the patrol loop DataRanger will run")
    b = sub.add_parser("bench", help="generate a DQ-Repair case and run the detectors on it")
    b.add_argument("--size", choices=["small", "medium", "large"], default="small")
    b.add_argument("--seed", type=int, default=1)
    dm = sub.add_parser("demo", help="patrol a generated table with planted problems and write the report")
    dm.add_argument("--out", default="dataranger-demo")
    dm.add_argument("--seed", type=int, default=7)
    pt = sub.add_parser("patrol", help="patrol your own DuckDB tables")
    pt.add_argument("--db", required=True)
    pt.add_argument("--table", required=True)
    pt.add_argument("--ref-table", required=True)
    pt.add_argument("--manifest-table", required=True, help="control totals: date, segment ('*' = total), n, amount")
    pt.add_argument("--key", default="txn_id")
    pt.add_argument("--reference", default="customer_id")
    pt.add_argument("--ref-key", default="customer_id")
    pt.add_argument("--date", default="txn_date")
    pt.add_argument("--segment", default="channel")
    pt.add_argument("--category", default=None)
    pt.add_argument("--amount", default="amount")
    pt.add_argument("--threshold", type=float, default=None, help="gate threshold (default: benchmark-calibrated)")
    pt.add_argument("--out", default="dataranger-report")
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
        lab = pd.read_csv(args.labels)
        verdict = dict(zip(lab["partition"].astype(str), lab["verdict"].str.lower().eq("right")))
        recs = []
        for f in args.audit:
            for line in open(f):
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
        n = {d: sum(r["decision"] == d for r in res.incidents) for d in ("auto-fixed", "no action needed", "escalate")}
        print(f"{len(res.incidents)} incidents: {n['auto-fixed']} fixed, {n['no action needed']} checked and left alone, "
              f"{n['escalate']} for you. Report: {args.out}/dashboard.html")
        return 0
    if args.command == "patrol":
        from pathlib import Path
        from .patrol import DEFAULT_LAMBDA, Mapping, from_duckdb, patrol, write_outputs
        facts, refs, man = from_duckdb(args.db, args.table, args.ref_table, args.manifest_table)
        mp = Mapping(key=args.key, reference=args.reference, date=args.date, segment=args.segment,
                     category=args.category, amount=args.amount, ref_key=args.ref_key)
        res = patrol(facts, refs, man, mp, lam=args.threshold if args.threshold is not None else DEFAULT_LAMBDA)
        write_outputs(res, Path(args.out), f"DataRanger patrol: {args.table}", table=args.table)
        print(f"{len(res.incidents)} incidents. Report: {args.out}/dashboard.html")
        return 0
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
    if args.command == "plan":
        for i, (name, what) in enumerate(STAGES, 1):
            print(f"{i}. {name}: {what}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
