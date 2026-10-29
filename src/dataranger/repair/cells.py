"""Cell-level repair for tables without a source of record (Hospital, Flights, Beers).

With no control totals to reconcile against, the evidence for a candidate value is internal:
how strongly the table's own dependencies or a deterministic normalization support it. The
same Learn-then-Test gate then bounds the share of applied changes that are wrong, using a
labeled calibration half.
"""
from __future__ import annotations

import re
from collections import Counter

import pandas as pd

from ..bench.detect_cells import NULL_TOKENS, NUM_PREFIX, unit_noise


def _num(v: str) -> str | None:
    m = NUM_PREFIX.match(v)
    if not m:
        return None
    x = float(m.group(0))
    return str(int(x)) if x.is_integer() else repr(round(x, 6)).rstrip("0").rstrip(".")


def candidates(df: pd.DataFrame, entity_key: str | None = None, min_group: int = 3) -> list[dict]:
    """Candidate repairs: {row, col, value, support, source}. Support is in [0, 1]; higher is better."""
    out: dict[tuple, dict] = {}

    def add(row, col, value, support, source, votes=1, rivals=0, share=1.0):
        if value == df.at[row, col]:
            return
        k = (row, col)
        if k not in out or support > out[k]["support"]:
            out[k] = {"row": row, "col": col, "value": value, "support": float(support), "source": source,
                      "votes": votes, "rivals": rivals, "share": float(share)}

    n = len(df)
    # 1. Dependencies A -> B: within each A group, the majority B value. A cell repaired the same
    #    way by several independent dependencies is better supported, so support counts the votes.
    votes: dict[tuple, dict] = {}
    for a in [c for c in df.columns if 1 < df[c].nunique() < n]:
        groups = df.groupby(a, sort=False).indices
        for b in df.columns:
            if b == a or df[b].nunique() >= 0.95 * n:
                continue
            col = df[b].to_numpy()
            agree = total = 0
            stats = []
            for idx in groups.values():
                if len(idx) < min_group:
                    continue
                top, k = Counter(col[idx]).most_common(1)[0]
                agree += k
                total += len(idx)
                stats.append((idx, top, k))
            if not total or agree / total < 0.9:
                continue
            for idx, top, k in stats:
                if k / len(idx) > 0.5:
                    for i in idx:
                        if col[i] != top:
                            v = votes.setdefault((int(df.index[i]), b), {})
                            v.setdefault(top, []).append(k / len(idx))
    for (row, b), v in votes.items():
        top = max(v, key=lambda x: (len(v[x]), sum(v[x])))
        rivals = sum(len(x) for val, x in v.items() if val != top)
        # support grows with agreeing dependencies and shrinks with disagreeing ones; kept in [0, 1)
        n_votes = len(v[top]) - rivals
        if n_votes > 0:
            add(row, b, top, 1 - 0.5 ** n_votes * (1.5 - sum(v[top]) / len(v[top])), "dep",
                votes=len(v[top]), rivals=rivals, share=sum(v[top]) / len(v[top]))
    # 2. Entity majority (multi-source tables such as Flights).
    if entity_key:
        for _, idx in df.groupby(entity_key, sort=False).indices.items():
            if len(idx) < min_group:
                continue
            for c in df.columns:
                if c == entity_key:
                    continue
                col = df[c].to_numpy()[idx]
                top, k = Counter(col).most_common(1)[0]
                share = k / len(idx)
                if share > 0.5:
                    for i, v in zip(idx, col):
                        if v != top:
                            add(int(df.index[i]), c, top, share * min(1.0, len(idx) / 10), "entity",
                                votes=int(k), rivals=int(len(idx) - k), share=share)
    # 3. Normalizations: inconsistent units, disguised nulls, codes leaked into a neighbour field.
    for i, c in unit_noise(df):
        y = _num(df.at[i, c].strip())
        if y is not None:
            add(int(i), c, y, 0.9, "units")
    for c in df.columns:
        for i in df.index[df[c].str.strip().str.lower().isin(NULL_TOKENS)]:
            add(int(i), c, "", 0.8, "null_token")
    short = [c for c in df.columns if 0 < df[c].str.len().median() <= 3 and df[c].nunique() >= 5]
    for s in short:
        domain = set(df[s][df[s].ne("")])
        for c in df.columns:
            if c == s:
                continue
            for i in df.index[df[c].str.contains(" ") & df[s].eq("")]:
                head, _, last = df.at[i, c].rpartition(" ")
                if last in domain:
                    add(int(i), c, head, 0.95, "leak")
                    add(int(i), s, last, 0.95, "leak")
    return list(out.values())


def score(cands: list[dict], clean: pd.DataFrame, dirty: pd.DataFrame) -> list[dict]:
    """Attach ground truth to each candidate: is the proposed value the clean value?"""
    for c in cands:
        c["correct"] = clean.at[c["row"], c["col"]] == c["value"]
        c["was_error"] = clean.at[c["row"], c["col"]] != dirty.at[c["row"], c["col"]]
    return cands


FEATURES = ["support", "votes", "rivals", "share"]


def featurize(cands: list[dict], columns: list[str]) -> "pd.DataFrame":
    """Numeric features for the learned evidence score (the 'learn' half of Learn-then-Test)."""
    sources = ["dep", "entity", "units", "null_token", "leak"]
    X = pd.DataFrame([[c[f] for f in FEATURES] for c in cands], columns=FEATURES, dtype=float)
    for s in sources:
        X[f"src_{s}"] = [float(c["source"] == s) for c in cands]
    X["col"] = [columns.index(c["col"]) for c in cands]
    return X
