"""Synthetic source and warehouse tables for the DQ-Repair benchmark.

The source tables are the system of record. The warehouse tables are what a
pipeline loads from them. Errors are injected into the warehouse only, so the
source stays available as ground truth for verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

SIZES = {"small": 400, "medium": 2_000, "large": 8_000}  # mean transactions per day
CHANNELS = ["card", "ach", "wire", "p2p"]
CATEGORIES = ["grocery", "fuel", "travel", "dining", "utilities", "retail", "health"]
CAT_MEDIAN = {"grocery": 45, "fuel": 40, "travel": 320, "dining": 38, "utilities": 110, "retail": 70, "health": 90}

# The contract the warehouse table is expected to follow.
TXN_SCHEMA = {
    "txn_id": "BIGINT",
    "customer_id": "BIGINT",
    "txn_date": "DATE",
    "channel": "VARCHAR",
    "category": "VARCHAR",
    "amount": "DOUBLE",
}


@dataclass
class Instance:
    """One benchmark instance: source of record, the clean warehouse, and metadata."""

    seed: int
    size: str
    days: list[date]
    customers: pd.DataFrame
    source_txn: pd.DataFrame
    meta: dict = field(default_factory=dict)

    def clean_warehouse(self) -> dict[str, pd.DataFrame]:
        """The warehouse as it should look: a faithful copy of the source."""
        return {"customers": self.customers.copy(), "transactions": self.source_txn.copy()}


def generate(seed: int = 1, size: str = "medium", n_days: int = 60, start: date = date(2026, 3, 1)) -> Instance:
    rng = np.random.default_rng(seed)
    per_day = SIZES[size]
    n_customers = max(500, per_day * 5)
    customers = pd.DataFrame({
        "customer_id": np.arange(1, n_customers + 1, dtype=np.int64),
        "segment": rng.choice(["retail", "premier", "small_business"], n_customers, p=[0.75, 0.15, 0.10]),
        "state": rng.choice(["IL", "CA", "TX", "NY", "FL", "WA", "OH"], n_customers),
    })

    days = [start + timedelta(days=i) for i in range(n_days)]
    weekday_factor = np.array([1.05, 1.0, 1.0, 1.02, 1.15, 0.85, 0.75])  # Mon..Sun
    frames, next_id = [], 1
    for d in days:
        n = int(rng.poisson(per_day * weekday_factor[d.weekday()] * rng.normal(1.0, 0.04)))
        cat = rng.choice(CATEGORIES, n, p=[0.25, 0.15, 0.05, 0.2, 0.1, 0.18, 0.07])
        med = np.array([CAT_MEDIAN[c] for c in cat], float)
        amount = np.round(med * rng.lognormal(0.0, 0.6, n), 2)
        frames.append(pd.DataFrame({
            "txn_id": np.arange(next_id, next_id + n, dtype=np.int64),
            "customer_id": rng.integers(1, n_customers + 1, n).astype(np.int64),
            "txn_date": pd.Timestamp(d),
            "channel": rng.choice(CHANNELS, n, p=[0.6, 0.2, 0.05, 0.15]),
            "category": cat,
            "amount": amount,
        }))
        next_id += n
    txn = pd.concat(frames, ignore_index=True)
    txn["txn_date"] = txn["txn_date"].dt.date
    return Instance(seed=seed, size=size, days=days, customers=customers, source_txn=txn,
                    meta={"rows": len(txn), "customers": n_customers, "days": n_days})
