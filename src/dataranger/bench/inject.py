"""Inject known data-quality issues into the warehouse copy and record the correct fix for each.

Six error types plus decoys. A decoy is an unusual but real pattern: it is applied to the
source as well as the warehouse, so the correct response is to leave the data alone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .tables import Instance

ERROR_TYPES = ["duplicates", "versioned_duplicates", "late_partition", "schema_drift", "broken_refs", "unit_scale", "silent_drop",
               "partial_late", "rekeyed_duplicates"]
DECOY_TYPES = ["volume_spike", "big_ticket_day"]

# The decision a careful engineer would make for each type.
EXPECTED = {
    "duplicates": ("dedupe", "auto"),
    "versioned_duplicates": ("dedupe_keep_latest", "auto"),
    "late_partition": ("reload_partition", "auto"),
    "schema_drift": ("remap_column", "escalate"),
    "broken_refs": ("quarantine", "escalate"),
    "unit_scale": ("rescale", "auto"),
    "silent_drop": ("reload_partition", "auto"),
    "partial_late": ("reload_partition", "auto"),
    "rekeyed_duplicates": ("dedupe_business", "auto"),
    "volume_spike": ("none", "none"),
    "big_ticket_day": ("none", "none"),
}

DEFAULT_MIX = {"duplicates": 2, "versioned_duplicates": 2, "late_partition": 2, "schema_drift": 1, "broken_refs": 2,
               "unit_scale": 2, "silent_drop": 2, "partial_late": 2, "rekeyed_duplicates": 2,
               "volume_spike": 2, "big_ticket_day": 2}


@dataclass
class Issue:
    issue_id: str
    kind: str
    table: str
    partition: str | None  # ISO date, or None for table-level issues
    decoy: bool
    fix: dict
    decision: str  # auto | escalate | none
    detail: dict = field(default_factory=dict)


@dataclass
class Case:
    """A benchmark case: the source of record, the dirty warehouse, and the answer key."""

    instance: Instance
    source: dict[str, pd.DataFrame]
    warehouse: dict[str, pd.DataFrame]
    issues: list[Issue]
    manifest: pd.DataFrame | None = None  # control totals the source publishes per partition

    def answer_key(self) -> list[dict]:
        return [asdict(i) for i in self.issues]


def inject(inst: Instance, mix: dict | None = None, seed: int | None = None, warmup: int = 14,
           p_stale: float = 0.15) -> Case:
    mix = dict(DEFAULT_MIX if mix is None else mix)
    rng = np.random.default_rng(inst.seed * 1000 + 7 if seed is None else seed)
    src = inst.clean_warehouse()
    kinds = [k for k, n in mix.items() for _ in range(n)]
    partition_kinds = [k for k in kinds if k != "schema_drift"]
    days = inst.days[warmup:]
    if len(partition_kinds) > len(days):
        raise ValueError("more partition-level issues than available days")
    chosen = rng.choice(len(days), len(partition_kinds), replace=False)
    slots = {i: days[j] for i, j in enumerate(chosen)}
    issues: list[Issue] = []

    # 1. Decoys change the source (they are real events), so apply them before copying.
    txn = src["transactions"]
    next_id = int(txn["txn_id"].max()) + 1
    for i, k in enumerate(partition_kinds):
        if k not in DECOY_TYPES:
            continue
        d = slots[i]
        day = txn[txn["txn_date"] == d]
        if k == "volume_spike":  # a promotion day: many more ordinary transactions
            factor = float(rng.uniform(1.8, 2.6))
            extra = day.sample(int(len(day) * (factor - 1)), replace=True, random_state=int(rng.integers(1 << 30))).copy()
            extra["txn_id"] = np.arange(next_id, next_id + len(extra), dtype=np.int64)
            extra["amount"] = np.round(extra["amount"].to_numpy() * rng.lognormal(0, 0.2, len(extra)), 2)
            next_id += len(extra)
            txn = pd.concat([txn, extra], ignore_index=True)
            detail = {"volume_factor": round(factor, 2)}
        else:  # a big-ticket day: real travel bookings push amounts up
            idx = day.sample(frac=float(rng.uniform(0.25, 0.4)), random_state=int(rng.integers(1 << 30))).index
            txn.loc[idx, "category"] = "travel"
            txn.loc[idx, "amount"] = np.round(320 * rng.lognormal(0.3, 0.5, len(idx)), 2)
            detail = {"rows_changed": int(len(idx))}
        issues.append(Issue(f"I{len(issues)+1:02d}", k, "transactions", d.isoformat(), True,
                            {"action": "none"}, "none", detail))
    src["transactions"] = txn.sort_values("txn_id", ignore_index=True)

    # 2. Errors change only the warehouse copy.
    wh = {k: v.copy() for k, v in src.items()}
    t = wh["transactions"]
    n_cust = int(src["customers"]["customer_id"].max())
    for i, k in enumerate(partition_kinds):
        if k in DECOY_TYPES:
            continue
        d = slots[i]
        mask = t["txn_date"] == d
        action, decision = EXPECTED[k]
        fix = {"action": action, "partition": d.isoformat()}
        if k == "duplicates":  # a retried load appends part of the partition again
            frac = float(rng.uniform(0.002, 0.08))
            dup = t[mask].sample(frac=frac, random_state=int(rng.integers(1 << 30)))
            t = pd.concat([t, dup], ignore_index=True)
            fix["key"] = "txn_id"
            detail = {"rows_added": int(len(dup))}
        elif k == "versioned_duplicates":
            # Settlement adjustments re-sent rows under the same key. The warehouse first loaded the
            # pre-adjustment amount; the settled version was appended later. Keeping the first copy
            # clears the duplicate-key alert and leaves the wrong amounts.
            idx = t[mask].sample(frac=float(rng.uniform(0.005, 0.05)), random_state=int(rng.integers(1 << 30))).index
            settled = t.loc[idx].copy()
            f_adj = rng.uniform(0.85, 1.15, len(idx))
            f_adj = np.where(np.abs(f_adj - 1) < 0.01, 1.02, f_adj)
            t.loc[idx, "amount"] = np.round(t.loc[idx, "amount"].to_numpy() * f_adj, 2)
            t = pd.concat([t, settled], ignore_index=True)
            fix.update({"key": "txn_id", "keep": "last"})
            detail = {"rows_adjusted": int(len(idx))}
        elif k == "late_partition":  # the file arrived after the job ran
            detail = {"rows_missing": int(mask.sum())}
            t = t[~mask]
        elif k == "broken_refs":  # a bad key mapping points rows at customers that do not exist
            idx = t[mask].sample(frac=float(rng.uniform(0.005, 0.03)), random_state=int(rng.integers(1 << 30))).index
            t.loc[idx, "customer_id"] = t.loc[idx, "customer_id"] + 10 * n_cust
            fix["rows"] = sorted(int(x) for x in t.loc[idx, "txn_id"])
            detail = {"rows_affected": int(len(idx))}
        elif k == "unit_scale":  # one upstream channel started sending cents
            ch = str(rng.choice(["card", "ach", "p2p"]))
            sel = mask & (t["channel"] == ch)
            t.loc[sel, "amount"] = np.round(t.loc[sel, "amount"] * 100, 2)
            fix.update({"factor": 0.01, "where": {"channel": ch}})
            detail = {"channel": ch, "rows_affected": int(sel.sum())}
        elif k == "silent_drop":  # a join change quietly drops one slice of the partition
            col = str(rng.choice(["category", "channel"]))
            options = t.loc[mask, col].value_counts()
            options = options[(options / mask.sum()).between(0.015, 0.2)]
            val = str(rng.choice(options.index)) if len(options) else str(t.loc[mask, col].mode()[0])
            sel = mask & (t[col] == val)
            detail = {"dropped": {col: val}, "rows_missing": int(sel.sum()), "share": round(float(sel.sum() / mask.sum()), 3)}
            t = t[~sel]
        elif k == "partial_late":  # the job ran before the last rows of the day landed
            frac = float(rng.uniform(0.003, 0.05))
            late = t[mask].sort_values("txn_id").tail(max(1, int(mask.sum() * frac))).index
            detail = {"rows_missing": int(len(late)), "share": round(frac, 4)}
            t = t.drop(late)
        elif k == "rekeyed_duplicates":  # a retry re-sent rows under new keys, so key checks pass
            dup = t[mask].sample(frac=float(rng.uniform(0.002, 0.05)), random_state=int(rng.integers(1 << 30))).copy()
            start = int(t["txn_id"].max()) + 1_000_000
            dup["txn_id"] = np.arange(start, start + len(dup), dtype=np.int64)
            t = pd.concat([t, dup], ignore_index=True)
            fix["key"] = ["customer_id", "amount", "category", "channel"]
            detail = {"rows_added": int(len(dup))}
        issues.append(Issue(f"I{len(issues)+1:02d}", k, "transactions", d.isoformat(), False, fix, decision, detail))

    if "schema_drift" in kinds:  # upstream renamed a column from some day onward
        d = days[int(rng.integers(len(days) // 2, len(days)))]
        sel = t["txn_date"] >= d
        t["payment_channel"] = pd.Series(pd.NA, index=t.index, dtype="object")
        t.loc[sel, "payment_channel"] = t.loc[sel, "channel"]
        t.loc[sel, "channel"] = pd.NA
        issues.append(Issue(f"I{len(issues)+1:02d}", "schema_drift", "transactions", None, False,
                            {"action": "remap_column", "from": "payment_channel", "to": "channel", "since": d.isoformat()},
                            "escalate", {"since": d.isoformat(), "rows_affected": int(sel.sum())}))

    wh["transactions"] = t.reset_index(drop=True)
    return Case(instance=inst, source=src, warehouse=wh, issues=issues,
                manifest=make_manifest(src["transactions"], rng, p_stale))


def make_manifest(txn: pd.DataFrame, rng: np.random.Generator, p_stale: float = 0.15) -> pd.DataFrame:
    """Control totals per partition and channel, as a source system would publish them.

    With probability p_stale a partition's manifest was written before its last rows arrived,
    so it undercounts by 0.1 to 1 percent. Verification has to live with that noise.
    """
    keep = []
    stale = {}
    for d, g in txn.groupby("txn_date", sort=True):
        if rng.random() < p_stale:
            m = max(1, int(len(g) * rng.uniform(0.001, 0.01)))
            g = g.drop(g.sample(m, random_state=int(rng.integers(1 << 30))).index)
            stale[d] = m
        keep.append(g)
    k = pd.concat(keep)
    by_ch = k.groupby(["txn_date", "channel"]).agg(n=("txn_id", "size"), amount=("amount", "sum")).reset_index()
    tot = k.groupby("txn_date").agg(n=("txn_id", "size"), amount=("amount", "sum")).reset_index()
    tot["channel"] = "*"
    m = pd.concat([tot, by_ch], ignore_index=True)
    m["stale_rows"] = m["txn_date"].map(stale).fillna(0).astype(int).where(m["channel"] == "*", 0)
    return m.sort_values(["txn_date", "channel"], ignore_index=True)
