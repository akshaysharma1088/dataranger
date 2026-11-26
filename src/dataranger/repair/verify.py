"""Source-grounded verification (stage 4).

The verifier never sees the source rows. It sees what a source system usually publishes:
control totals per partition, optionally per channel. A fix is judged by how far the fixed
partition still is from those totals (the residual). Manifests can be stale, so even a
correct fix may leave a small residual.
"""
from __future__ import annotations

import hashlib

import pandas as pd


def table_fingerprint(part: pd.DataFrame) -> str:
    """A content fingerprint of a repaired partition, order-independent: equal fingerprints mean
    two candidates produced identical data; different fingerprints mean they differ (issue #91).
    Not a security hash -- just a cheap, deterministic way to compare two repaired tables."""
    if not len(part):
        return hashlib.sha256(b"<empty>").hexdigest()
    cols = ["txn_id", "customer_id", "channel", "category", "amount"]
    a = part[cols].assign(amount=part["amount"].round(2)).sort_values("txn_id").reset_index(drop=True)
    text = "\n".join(",".join(map(str, row)) for row in a.itertuples(index=False))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_hash(s: str) -> int:
    return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)


def hash_total(df: pd.DataFrame, key_col: str = "txn_id", value_col: str = "amount") -> float:
    """Sum of a stable hash of (key, value) per row: a control total with no arithmetic meaning of
    its own (the classic batch-processing "hash total"), used only to notice that a row's value
    changed even when aggregate counts and sums still match (issue #87) -- for example two amounts
    swapped between otherwise-unchanged rows. It is additive like `n` and `amount`, so a few stale
    (missing) rows shift it by a small amount rather than breaking it outright."""
    if not len(df):
        return 0.0
    h = (df[key_col].astype(str) + "|" + df[value_col].round(2).astype(str)).map(_stable_hash)
    return float(h.sum())


def _hash_gap(part: pd.DataFrame, tot: pd.Series) -> float:
    """The hash total's share of the gap.

    One changed row moves a hash total by about one row's worth, so as a fraction of the total it is
    lost on any day with more than a couple of hundred rows: a swap of two amounts would stay under
    any useful threshold. So when the row count and the amount already match the control totals
    exactly, nothing is missing or extra, and the hash total has to match exactly as well; any
    difference counts as a full gap (1.0). When the count or the amount is off (late rows, a stale
    manifest), an exact match is impossible and the hash total is compared as a fraction, as before.
    """
    diff = abs(hash_total(part) - float(tot["hash_total"]))
    scale = max(abs(float(tot["hash_total"])), 1.0)
    settled = len(part) == tot["n"] and abs(float(part["amount"].sum()) - float(tot["amount"])) <= 0.005
    if settled:  # the tolerance only absorbs float rounding of very large totals; one row moves it by ~2**31
        return 1.0 if diff > max(0.5, 1e-12 * scale) else 0.0
    return diff / scale


def distinct_count(df: pd.DataFrame, col: str) -> int:
    return int(df[col].nunique()) if col in df.columns and len(df) else 0


def residual(part: pd.DataFrame, manifest: pd.DataFrame, d, granularity: str = "channel") -> float:
    """Largest relative gap between the partition and its control totals (0 = exact match).

    Beyond `n` and `amount`, if the control totals carry any of the optional extra measures from
    issue #87 -- `hash_total`, `min_amount`, `max_amount`, or a `distinct_<column>` count -- each
    present measure is folded into the same max-gap statistic. The hash total is exact when the day's
    count and amount already match (see `_hash_gap`). A manifest without those columns (the default)
    is checked exactly as before."""
    m = manifest[manifest["txn_date"] == d]
    if m.empty:
        return float("inf")
    gaps = []
    tot = m[m["channel"] == "*"].iloc[0]
    gaps.append(abs(len(part) - tot["n"]) / max(tot["n"], 1))
    gaps.append(abs(part["amount"].sum() - tot["amount"]) / max(abs(tot["amount"]), 1.0))
    if "hash_total" in tot.index and pd.notna(tot["hash_total"]):
        gaps.append(_hash_gap(part, tot))
    if len(part):
        if "min_amount" in tot.index and pd.notna(tot["min_amount"]):
            gaps.append(abs(part["amount"].min() - tot["min_amount"]) / max(abs(tot["min_amount"]), 1.0))
        if "max_amount" in tot.index and pd.notna(tot["max_amount"]):
            gaps.append(abs(part["amount"].max() - tot["max_amount"]) / max(abs(tot["max_amount"]), 1.0))
    for col in tot.index:
        if col.startswith("distinct_") and pd.notna(tot[col]):
            key_col = col[len("distinct_"):]
            if key_col in part.columns:
                gaps.append(abs(distinct_count(part, key_col) - tot[col]) / max(tot[col], 1))
    if granularity in ("channel", "channel+category"):
        obs = part.groupby("channel").agg(n=("txn_id", "size"), amount=("amount", "sum"))
        seg = m[(m["channel"] != "*") & ~m["channel"].astype(str).str.contains("/")]
        for _, r in seg.iterrows():
            o = obs.loc[r["channel"]] if r["channel"] in obs.index else pd.Series({"n": 0, "amount": 0.0})
            gaps.append(abs(o["n"] - r["n"]) / max(r["n"], 1))
            gaps.append(abs(o["amount"] - r["amount"]) / max(abs(r["amount"]), 1.0))
    if granularity == "channel+category":  # a source that also publishes totals per channel and category
        obs = part.groupby(["channel", "category"]).agg(n=("txn_id", "size"), amount=("amount", "sum"))
        obs.index = [f"{a}/{b}" for a, b in obs.index]
        for _, r in m[m["channel"].astype(str).str.contains("/")].iterrows():
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
