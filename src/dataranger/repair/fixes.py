"""Fix templates (stage 3 of Detect, Fix, Prove).

Every fix works on a copy of one partition and returns the new version; nothing is
overwritten in place. Each template has a risk tier: 'auto' fixes may be applied by the
gate once proven, 'escalate' fixes always go to a person.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TIER = {"none": "auto", "dedupe": "auto", "dedupe_business": "auto", "reload": "auto", "rescale": "auto",
        "trim": "escalate", "trim_value": "escalate", "quarantine": "escalate", "remap": "escalate"}


@dataclass(frozen=True)
class Fix:
    action: str
    params: tuple = field(default_factory=tuple)  # sorted (key, value) pairs, hashable

    @property
    def tier(self) -> str:
        return TIER[self.action]

    def p(self, key, default=None):
        return dict(self.params).get(key, default)

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params)
        return f"{self.action}({args})"


def make(action: str, **params) -> Fix:
    return Fix(action, tuple(sorted(params.items())))


def apply(fix: Fix, part: pd.DataFrame, source_part: pd.DataFrame | None, customers: pd.DataFrame) -> pd.DataFrame:
    """Return the fixed version of one partition. Raises if the fix cannot run."""
    p = part.copy()
    a = fix.action
    if a == "none":
        return p
    if a == "dedupe":
        return p.drop_duplicates(subset=[fix.p("key", "txn_id")], keep="first")
    if a == "dedupe_business":  # same customer, amount, category and channel on one day
        return p.sort_values("txn_id").drop_duplicates(subset=["customer_id", "amount", "category", "channel"])
    if a == "reload":
        if source_part is None:
            raise ValueError("source partition is outside the retention window")
        return source_part.copy()
    if a == "rescale":
        sel = np.ones(len(p), bool) if fix.p("channel") is None else (p["channel"] == fix.p("channel")).to_numpy()
        p.loc[sel, "amount"] = np.round(p.loc[sel, "amount"] * fix.p("factor"), 2)
        return p
    if a == "trim":  # cut the partition back to its usual volume, dropping the newest rows
        return p.sort_values("txn_id").head(int(fix.p("to")))
    if a == "trim_value":
        col, val, share = fix.p("column"), fix.p("value"), fix.p("share")
        sel = p[col] == val
        others = int((~sel).sum())
        keep = int(round(share * others / (1 - share)))
        drop = p[sel].sort_values("txn_id").index[keep:]
        return p.drop(drop)
    if a == "quarantine":
        return p[p["customer_id"].isin(customers["customer_id"])]
    raise ValueError(f"unknown action {a}")
