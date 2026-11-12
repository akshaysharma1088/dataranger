"""Run the patrol loop on your own data.

Inputs: a DuckDB database (or CSV) with a fact table partitioned by date, a reference table, and
the source's control totals (manifest). Column names are mapped to DataRanger's internal ones.
Outputs, all next to each other in an output folder:
  * fixed partitions written as a NEW table version (the input is never modified)
  * audit.jsonl, one line per incident with evidence and decision
  * dashboard.html, the morning report: what broke, what was fixed, what needs a person
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from .bench.inject import Case
from .bench.tables import Instance
from .repair import fixes as F
from .repair.agent import run
from .repair.gate import select

# Threshold calibrated on the DQ-Repair benchmark for alpha = 1% (per-channel control totals).
# Recalibrate on your own labeled decisions with `dataranger calibrate` as soon as you have them.
DEFAULT_LAMBDA = 0.0135

INTERNAL = ("txn_id", "customer_id", "txn_date", "channel", "category", "amount")


@dataclass
class Mapping:
    key: str = "txn_id"
    reference: str = "customer_id"
    date: str = "txn_date"
    segment: str = "channel"
    category: str | None = "category"
    amount: str = "amount"
    ref_key: str = "customer_id"

    def _require_columns(self, df: pd.DataFrame, required: list[tuple[str, str]], table_label: str) -> None:
        """Raise SystemExit if any mapped column is missing from *df*."""
        missing = [(name, flag) for name, flag in required if name not in df.columns]
        if not missing:
            return
        names = ", ".join(repr(n) for n, _ in missing)
        flags = ", ".join(f for _, f in missing)
        available = ", ".join(map(str, df.columns.tolist()))
        raise SystemExit(
            f"{table_label} has no column {names} ({flags}); columns are: {available}"
        )

    def validate_facts(self, df: pd.DataFrame) -> None:
        required = [
            (self.key, "--key"),
            (self.reference, "--reference"),
            (self.date, "--date"),
            (self.segment, "--segment"),
            (self.amount, "--amount"),
        ]
        if self.category:
            required.append((self.category, "--category"))
        self._require_columns(df, required, "facts table")

    def validate_refs(self, df: pd.DataFrame) -> None:
        self._require_columns(df, [(self.ref_key, "--ref-key")], "reference table")

    def validate_manifest(self, df: pd.DataFrame) -> None:
        required = [
            (self.date, "--date"),
            (self.segment, "--segment"),
            ("n", "n"),
            ("amount", "--amount / amount"),
        ]
        self._require_columns(df, required, "control totals (manifest)")

    def to_internal(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_facts(df)
        m = {self.key: "txn_id", self.reference: "customer_id", self.date: "txn_date",
             self.segment: "channel", self.amount: "amount"}
        if self.category:
            m[self.category] = "category"
        out = df.rename(columns=m)
        if "category" not in out:
            out["category"] = "all"
        out["txn_date"] = pd.to_datetime(out["txn_date"]).dt.date
        return out[list(INTERNAL)]

    def to_user(self, df: pd.DataFrame) -> pd.DataFrame:
        m = {"txn_id": self.key, "customer_id": self.reference, "txn_date": self.date,
             "channel": self.segment, "amount": self.amount}
        if self.category:
            m["category"] = self.category
        else:
            df = df.drop(columns=["category"])
        return df.rename(columns=m)


@dataclass
class PatrolResult:
    incidents: list[dict] = field(default_factory=list)
    fixed_table: pd.DataFrame | None = None
    days: list = field(default_factory=list)
    lam: float = DEFAULT_LAMBDA
    rows: int = 0


def patrol(facts: pd.DataFrame, refs: pd.DataFrame, manifest: pd.DataFrame, mapping: Mapping,
           lam: float = DEFAULT_LAMBDA, source_facts: pd.DataFrame | None = None, retention_days: int = 14) -> PatrolResult:
    """Detect, propose, verify and gate. `manifest` has columns date, segment ('*' for the total), n, amount."""
    mapping.validate_facts(facts)
    mapping.validate_refs(refs)
    mapping.validate_manifest(manifest)
    t = mapping.to_internal(facts)
    cust = refs.rename(columns={mapping.ref_key: "customer_id"})[["customer_id"]]
    man = manifest.rename(columns={mapping.date: "txn_date", mapping.segment: "channel"}).copy()
    man["txn_date"] = pd.to_datetime(man["txn_date"]).dt.date
    days = sorted(pd.date_range(min(t["txn_date"]), max(t["txn_date"])).date)
    src = mapping.to_internal(source_facts) if source_facts is not None else t.iloc[:0]
    inst = Instance(seed=0, size="user", days=days, customers=cust, source_txn=src)
    case = Case(instance=inst, source={"transactions": src, "customers": cust},
                warehouse={"transactions": t, "customers": cust}, issues=[], manifest=man)
    recs = run(case, retention_days=retention_days if source_facts is not None else -10**6)
    fixed = t.copy()
    for r in recs:
        r["decision"] = "escalate"
        if r["table_level"]:
            continue
        c = select(r)
        if c and c["tier"] == "auto" and c["residual"] <= lam:
            r["decision"] = "no action needed" if c["action"] == "none" else "auto-fixed"
            r["applied"] = c["fix"]
            if c["action"] != "none":
                d = pd.Timestamp(r["partition"]).date()
                part = fixed[fixed["txn_date"] == d]
                sp = src[src["txn_date"] == d] if len(src) else None
                fx = _parse(c["fix"])
                new = F.apply(fx, part, sp, cust)
                fixed = pd.concat([fixed[fixed["txn_date"] != d], new], ignore_index=True)
        r["chosen"] = c
    return PatrolResult(recs, mapping.to_user(fixed.sort_values(["txn_date", "txn_id"], ignore_index=True)),
                        days=[d.isoformat() for d in days], lam=lam, rows=len(t))


def _parse(s: str) -> F.Fix:
    action, _, args = s.partition("(")
    params = {}
    for kv in filter(None, args.rstrip(")").split(", ")):
        k, v = kv.split("=", 1)
        try:
            params[k] = float(v) if k in ("factor", "share") else (int(v) if k == "to" else v)
        except ValueError:
            params[k] = v
    return F.make(action, **params)


def write_outputs(res: PatrolResult, out: Path, title: str = "DataRanger patrol report", table: str = "transactions") -> None:
    from .report import render
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "audit.jsonl", "w", encoding="utf-8") as f:
        for r in res.incidents:
            clean = lambda c: {k: v for k, v in c.items() if k != "correct"} if c else c  # drop benchmark-only truth
            rec = {k: v for k, v in r.items() if k not in ("candidates", "kinds", "chosen")}
            rec |= {"chosen": clean(r.get("chosen")), "candidates": [clean(c) for c in r["candidates"]]}
            f.write(json.dumps(rec, default=str) + "\n")
    rows = []
    for r in res.incidents:
        chosen = r.get("chosen") or {}
        rows.append({"partition": r.get("partition"), "problems": "; ".join(r.get("detectors") or []),
                     "decision": r["decision"], "action": chosen.get("action"),
                     "tier": chosen.get("tier"), "residual": chosen.get("residual")})
    pd.DataFrame(rows, columns=["partition", "problems", "decision", "action", "tier", "residual"]).to_csv(
        out / "incidents.csv", index=False)
    if res.fixed_table is not None:
        res.fixed_table.to_csv(out / "fixed_version.csv", index=False)
    meta = {"table": table, "days": res.days, "lambda": res.lam, "rows": res.rows, "alpha": 0.01}
    (out / "dashboard.html").write_text(render(res.incidents, title, run_date=date.today().isoformat(), meta=meta), encoding="utf-8")


def _quoted(name: str) -> str:
    """Quote each dot-separated part of a table name, so `gold.payments` reads as
    schema `gold` table `payments` instead of one identifier."""
    return ".".join('"' + part.replace('"', '""') + '"' for part in name.split("."))


def from_duckdb(db: str, table: str, ref_table: str, manifest_table: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(db, read_only=True)
    try:
        return (con.execute(f'select * from {_quoted(table)}').df(), con.execute(f'select * from {_quoted(ref_table)}').df(),
                con.execute(f'select * from {_quoted(manifest_table)}').df())
    finally:
        con.close()


def from_files(facts: str, refs: str, manifest: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read facts, refs and manifest straight from CSV or Parquet files.

    Mirrors from_duckdb for people who export a few files before they have a
    database to point at. The reader follows the extension: .csv/.tsv go through
    DuckDB's ``read_csv_auto``, .parquet/.pq through ``read_parquet``.
    """
    con = duckdb.connect()
    try:
        def one(path: str) -> pd.DataFrame:
            suffix = Path(path).suffix.lower()
            if suffix in (".csv", ".tsv"):
                return con.execute("select * from read_csv_auto(?)", [path]).df()
            if suffix in (".parquet", ".pq"):
                return con.execute("select * from read_parquet(?)", [path]).df()
            raise ValueError(f"unsupported file type for {path!r}: expected .csv, .tsv, .parquet or .pq")

        return one(facts), one(refs), one(manifest)
    finally:
        con.close()
