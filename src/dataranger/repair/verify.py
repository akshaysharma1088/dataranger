"""Source-grounded verification (stage 4).

The verifier never sees the source rows. It sees what a source system usually publishes:
control totals per partition, optionally per channel. A fix is judged by how far the fixed
partition still is from those totals (the residual). Manifests can be stale, so even a
correct fix may leave a small residual.
"""
from __future__ import annotations

import pandas as pd


def residual(part: pd.DataFrame, manifest: pd.DataFrame, d, granularity: str = "channel") -> float:
    """Largest relative gap between the partition and its control totals (0 = exact match)."""
    m = manifest[manifest["txn_date"] == d]
    if m.empty:
        return float("inf")
    gaps = []
    tot = m[m["channel"] == "*"].iloc[0]
    gaps.append(abs(len(part) - tot["n"]) / max(tot["n"], 1))
    gaps.append(abs(part["amount"].sum() - tot["amount"]) / max(abs(tot["amount"]), 1.0))
    if granularity == "channel":
        obs = part.groupby("channel").agg(n=("txn_id", "size"), amount=("amount", "sum"))
        for _, r in m[m["channel"] != "*"].iterrows():
            o = obs.loc[r["channel"]] if r["channel"] in obs.index else pd.Series({"n": 0, "amount": 0.0})
            gaps.append(abs(o["n"] - r["n"]) / max(r["n"], 1))
            gaps.append(abs(o["amount"] - r["amount"]) / max(abs(r["amount"]), 1.0))
    return float(max(gaps))


COLS = ["txn_id", "customer_id", "channel", "category", "amount"]


def matches_truth(part: pd.DataFrame, truth: pd.DataFrame) -> bool:
    """Ground truth only, for scoring: the partition equals the source partition row for row."""
    if len(part) != len(truth):
        return False
    a = part[COLS].assign(amount=part["amount"].round(2)).sort_values("txn_id").reset_index(drop=True)
    b = truth[COLS].assign(amount=truth["amount"].round(2)).sort_values("txn_id").reset_index(drop=True)
    return bool((a.astype(str).to_numpy() == b.astype(str).to_numpy()).all())
