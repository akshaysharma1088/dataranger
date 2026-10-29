"""The patrol loop on one benchmark case: detect, propose candidate fixes, verify each one.

This module produces one record per incident with every candidate's evidence (residual
against the source manifest) and, for scoring only, whether the candidate is truly correct.
The gate (gate.py) then decides which incidents are fixed automatically.

The proposer here enumerates fix templates from the detector findings, with no language
model. An LLM proposer plugs in at `propose` and is compared against this one.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ..bench.detect import Finding, detect
from ..bench.inject import Case
from . import fixes as F
from .verify import matches_truth, residual

# What a rules-based auto-fixer does for each alert, with no checks.
DEFAULT_FIX = {"duplicate_keys": "dedupe", "duplicate_rows": "dedupe_business", "missing_partition": "reload", "orphan_keys": "quarantine",
               "volume_drop": "reload", "mix_drop": "reload", "reconcile_gap": "reload", "volume_rise": "trim", "mix_rise": "trim_value",
               "scale_shift": "rescale"}


CHANNELS = ("card", "ach", "p2p", "wire")  # the benchmark's segments, in the order v0.1 enumerated them


def propose(findings: list[Finding], reload_ok: bool, channels: tuple = CHANNELS) -> tuple[list[F.Fix], F.Fix | None]:
    """All template candidates for one partition, and the naive default a rules engine would pick."""
    c: list[F.Fix] = [F.make("none")]
    default = None
    for f in findings:
        e = f.evidence
        if f.detector == "duplicate_keys":
            c += [F.make("dedupe", key="txn_id"), F.make("dedupe", key="txn_id", keep="last")]
        elif f.detector == "duplicate_rows":
            c.append(F.make("dedupe_business"))
        elif f.detector in ("missing_partition", "volume_drop", "mix_drop", "reconcile_gap") and reload_ok:
            c.append(F.make("reload"))
        elif f.detector == "orphan_keys":
            c.append(F.make("quarantine"))
        elif f.detector == "volume_rise":
            c += [F.make("dedupe", key="txn_id"), F.make("trim", to=int(e["typical"]))]
        elif f.detector == "mix_rise":
            c.append(F.make("trim_value", column=e["column"], value=e["value"], share=float(e["usual"])))
        elif f.detector == "scale_shift":
            c.append(F.make("rescale", factor=0.01))
            if "channel" in e:
                c.append(F.make("rescale", factor=0.01, channel=e["channel"]))
            for ch in channels:
                c.append(F.make("rescale", factor=0.01, channel=ch))
        if default is None and f.detector in DEFAULT_FIX:
            a = DEFAULT_FIX[f.detector]
            match = [x for x in c if x.action == a]
            if a == "rescale" and "channel" in e:
                match = [x for x in c if x.action == "rescale" and x.p("channel") == e["channel"]] or match
            default = match[0] if match else None
    return list(dict.fromkeys(c)), default


def invariants_hold(part: pd.DataFrame, customers: pd.DataFrame) -> bool:
    """Declared invariants a fix must leave true: a non-empty partition, unique keys, valid references.

    Control totals cannot see a corrupted key (counts and sums do not change), so verification
    checks the declared invariants as well as reconciling to the source.
    """
    return bool(len(part)) and not part["txn_id"].duplicated().any() and \
        bool(part["customer_id"].isin(customers["customer_id"]).all())


class Monitors:
    """The team's existing monitors, re-run on a single fixed partition (what a monitor-checked agent sees)."""

    def __init__(self, t: pd.DataFrame, customers: pd.DataFrame, z_volume=4.0, z_mix=5.0, scale_ratio=20.0):
        self.cust = set(customers["customer_id"])
        n = t.groupby("txn_date").size()
        w = pd.Series([pd.Timestamp(d).isoweekday() for d in n.index], index=n.index)
        self.vol = {k: (np.median(np.log(g)), max(1.4826 * np.median(np.abs(np.log(g) - np.median(np.log(g)))), 0.02))
                    for k, g in n.groupby(w)}
        self.med = t.dropna(subset=["channel"]).groupby(["txn_date", "channel"])["amount"].median().groupby("channel").median()
        self.mix = {}
        for col in ("category", "channel"):
            s = t.dropna(subset=[col]).groupby(["txn_date", col]).size().unstack(fill_value=0)
            self.mix[col] = s.div(s.sum(axis=1), axis=0).median()
        self.z_volume, self.z_mix, self.scale_ratio = z_volume, z_mix, scale_ratio

    def quiet(self, part: pd.DataFrame, d) -> bool:
        if part.empty or part["txn_id"].duplicated().any() or not part["customer_id"].isin(self.cust).all():
            return False
        med, mad = self.vol[pd.Timestamp(d).isoweekday()]
        if abs(np.log(len(part)) - med) / mad >= self.z_volume:
            return False
        for ch, g in part.dropna(subset=["channel"]).groupby("channel"):
            r = g["amount"].median() / self.med.get(ch, g["amount"].median())
            if r >= self.scale_ratio or r <= 1 / self.scale_ratio:
                return False
        for col, base in self.mix.items():
            share = part[col].value_counts(normalize=True)
            for v, p0 in base.items():
                if p0 > 0 and abs((share.get(v, 0.0) - p0) / np.sqrt(p0 * (1 - p0) / len(part))) >= self.z_mix:
                    return False
        return True


def context(part: pd.DataFrame, d, fs: list[Finding], manifest: pd.DataFrame, mon: "Monitors", reload_ok: bool) -> dict:
    """What an operator (or an LLM) sees about one incident: the alerts and the numbers behind them."""
    m = manifest[manifest["txn_date"] == d].set_index("channel")
    med, _ = mon.vol[pd.Timestamp(d).isoweekday()]
    ch = {}
    for c in sorted(set(m.index) - {"*"} | set(part["channel"].dropna())):
        g = part[part["channel"] == c]
        ch[c] = {"rows": int(len(g)), "source_rows": int(m.at[c, "n"]) if c in m.index else 0,
                 "amount": round(float(g["amount"].sum()), 2),
                 "source_amount": round(float(m.at[c, "amount"]), 2) if c in m.index else 0.0,
                 "median_amount": round(float(g["amount"].median()), 2) if len(g) else None,
                 "usual_median_amount": round(float(mon.med.get(c, float("nan"))), 2)}
    return {"date": pd.Timestamp(d).date().isoformat(), "weekday": pd.Timestamp(d).day_name(),
            "rows": int(len(part)), "typical_rows_for_weekday": int(round(float(np.exp(med)))),
            "source_rows": int(m.at["*", "n"]) if "*" in m.index else None,
            "duplicate_keys": int(part["txn_id"].duplicated().sum()),
            "duplicate_business_rows": int(part.duplicated(subset=["customer_id", "amount", "category", "channel"]).sum()),
            "unknown_customers": int((~part["customer_id"].isin(mon.cust)).sum()),
            "alerts": [{"detector": f.detector, **{k: v for k, v in f.evidence.items()}} for f in fs],
            "channels": ch, "reload_from_source_available": reload_ok}


def run(case: Case, granularity: str = "channel", retention_days: int = 14, reconcile: bool = True,
        extra: dict[str, F.Fix | None] | None = None, check_invariants: bool = True) -> list[dict]:
    """`extra` maps a partition to a fix from another proposer (an LLM); it is verified like the rest
    and marked with source 'llm'. A None value records that the proposer chose to escalate."""
    inst = case.instance
    wh = {k: v.copy() for k, v in case.warehouse.items()}
    src_t = case.source["transactions"]
    records: list[dict] = []
    findings = detect(wh, inst.days)

    # Table-level incidents (schema changes) always go to a person; the person applies the true fix.
    if any(f.partition is None or f.detector == "null_spike" for f in findings):
        drift = [i for i in case.issues if i.kind == "schema_drift"]
        if drift and "payment_channel" in wh["transactions"].columns:
            t = wh["transactions"]
            t["channel"] = t["channel"].fillna(t["payment_channel"])
            wh["transactions"] = t.drop(columns=["payment_channel"])
        records.append({"partition": None, "kinds": ["schema_drift"] if drift else ["false_alarm"],
                        "table_level": True, "candidates": []})
        findings = detect(wh, inst.days)

    t = wh["transactions"]
    seen = set(t["channel"].dropna().astype(str))
    channels = CHANNELS if seen <= set(CHANNELS) else tuple(sorted(seen))
    mon = Monitors(t, wh["customers"])
    issue_kinds: dict[str, list[str]] = {}
    for i in case.issues:
        if i.partition:
            issue_kinds.setdefault(i.partition, []).append(i.kind)
    last = max(inst.days)
    by_part: dict[str, list[Finding]] = {}
    for f in findings:
        if f.partition is not None and f.detector != "null_spike":
            by_part.setdefault(f.partition, []).append(f)
    if reconcile:  # source-aware monitor: any partition that does not match its control totals
        for d, part in t.groupby("txn_date"):
            gap = residual(part, case.manifest, d, granularity)
            if gap > 1e-9:
                by_part.setdefault(pd.Timestamp(d).date().isoformat(), []).append(
                    Finding("reconcile_gap", "transactions", pd.Timestamp(d).date().isoformat(), gap, {}))

    for p, fs in sorted(by_part.items()):
        d = pd.Timestamp(p).date()
        part = t[t["txn_date"] == d]
        truth = src_t[src_t["txn_date"] == d]
        reload_ok = d >= last - timedelta(days=retention_days)
        cands, default = propose(fs, reload_ok, channels)
        src_of = {fx: "template" for fx in cands}
        llm_fix = (extra or {}).get(p)
        if llm_fix is not None:
            if llm_fix in src_of:
                src_of[llm_fix] = "template+llm"
            else:
                cands.append(llm_fix)
                src_of[llm_fix] = "llm"
        rows = []
        for fx in cands:
            try:
                fixed = F.apply(fx, part, truth if reload_ok else None, wh["customers"])
            except ValueError:
                continue
            inv = invariants_hold(fixed, wh["customers"]) if check_invariants else bool(len(fixed))
            rows.append({"fix": str(fx), "action": fx.action, "tier": fx.tier, "invariants": inv, "source": src_of[fx],
                         "residual": residual(fixed, case.manifest, d, granularity) if inv else float("inf"),
                         "residual_partition": residual(fixed, case.manifest, d, "partition") if inv else float("inf"),
                         "changed": int(abs(len(fixed) - len(part)) + (fixed["amount"].sum() != part["amount"].sum())),
                         "correct": matches_truth(fixed, truth),
                         "monitors_quiet": mon.quiet(fixed, d) if len(fixed) else False})
        records.append({"partition": p, "kinds": issue_kinds.get(p, ["false_alarm"]), "table_level": False,
                        "detectors": sorted({f.detector for f in fs}), "reload_ok": reload_ok,
                        "default": str(default) if default else None, "candidates": rows,
                        "llm": str(llm_fix) if llm_fix is not None else None,
                        "context": context(part, d, fs, case.manifest, mon, reload_ok)})
    return records
