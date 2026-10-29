"""Deterministic cell-level detectors for the real datasets.

Two generic, configuration-free signals:
  * format outliers: a value whose character shape is rare in its column;
  * dependency violations: for a column pair A -> B that holds for almost every A value,
    a B value that disagrees with the majority for its A group.
No labels, no language model.
"""
from __future__ import annotations

import re
from collections import Counter

import pandas as pd


def shape(v: str) -> str:
    s = re.sub(r"[0-9]+", "9", v)
    s = re.sub(r"[A-Za-z]+", "a", s)
    return s


def format_outliers(df: pd.DataFrame, min_share: float = 0.01) -> set[tuple[int, str]]:
    out = set()
    for c in df.columns:
        shapes = df[c].map(shape)
        freq = shapes.value_counts(normalize=True)
        if freq.iloc[0] < 0.5:  # free-text column with no dominant format
            continue
        rare = set(freq[freq < min_share].index)
        out |= {(int(i), c) for i in df.index[shapes.isin(rare)]}
    return out


def dependency_violations(df: pd.DataFrame, min_support: float = 0.9, min_group: int = 2,
                          max_card: float = 0.95) -> set[tuple[int, str]]:
    """Flag minority values of B inside groups of A, for near-functional dependencies A -> B."""
    out = set()
    n = len(df)
    cols = [c for c in df.columns if df[c].nunique() < max_card * n]  # skip key-like columns as targets
    lhs_cols = [c for c in df.columns if 1 < df[c].nunique() < n]
    for a in lhs_cols:
        groups = df.groupby(a, sort=False).indices
        multi = {k: v for k, v in groups.items() if len(v) >= min_group}
        if not multi:
            continue
        for b in cols:
            if b == a:
                continue
            col = df[b].to_numpy()
            agree = total = 0
            minority = []
            for idx in multi.values():
                cnt = Counter(col[idx])
                top, k = cnt.most_common(1)[0]
                agree += k
                total += len(idx)
                if k < len(idx) and k / len(idx) > 0.5:
                    minority.extend(int(i) for i in idx if col[i] != top)
            if total and agree / total >= min_support:
                out |= {(int(df.index[i]), b) for i in minority}
    return out


NULL_TOKENS = {"n/a", "na", "null", "none", "not available", "-", "?", "nan"}
NUM = re.compile(r"^\s*[-+]?\d*\.?\d+\s*$")
NUM_PREFIX = re.compile(r"^\s*[-+]?\d*\.?\d+")


def missing_values(df: pd.DataFrame, max_empty: float = 0.1) -> set[tuple[int, str]]:
    """Disguised nulls such as 'N/A', and blanks in columns that are almost always filled."""
    out = set()
    for c in df.columns:
        v = df[c].str.strip().str.lower()
        out |= {(int(i), c) for i in df.index[v.isin(NULL_TOKENS)]}
        empty = v.eq("")
        if 0 < empty.mean() <= max_empty:
            out |= {(int(i), c) for i in df.index[empty]}
    return out


def unit_noise(df: pd.DataFrame, min_numeric: float = 0.5, min_pure: float = 0.2) -> set[tuple[int, str]]:
    """Inconsistent units in numeric columns: '12.0 oz' next to '16.0 ounce', or '0.05%' next to '0.05'.

    A column that always writes the same unit ('95%', '120 patients') is consistent and is left alone.
    """
    out = set()
    for c in df.columns:
        v = df[c].str.strip()
        filled = v[v.ne("") & ~v.str.lower().isin(NULL_TOKENS)]
        if filled.empty:
            continue
        lead = filled.str.match(NUM_PREFIX)
        pure = filled.str.match(NUM)
        if lead.mean() < min_numeric:
            continue
        suffix = filled[lead & ~pure].str.replace(NUM_PREFIX, "", regex=True).str.strip()
        suffix = suffix[~suffix.str.contains(r"\d")]  # '7:35 a.m.' is a time, not a number with a unit
        if suffix.empty:
            continue
        mixed = pure.mean() >= min_pure
        variants = suffix.value_counts(normalize=True)
        variants = variants[variants >= 0.01].index
        norm = [re.sub(r"[^a-z%]", "", x.lower()) for x in variants]
        spelled = len(set(norm)) < len(norm)  # the same unit written more than one way ('oz', 'OZ.')
        if mixed or spelled:
            out |= {(int(i), c) for i in suffix.index}
    return out


def token_leaks(df: pd.DataFrame) -> set[tuple[int, str]]:
    """A code that slipped into a neighbouring field: 'Longmont CO' in city while the state field is blank."""
    out = set()
    short = [c for c in df.columns if 0 < df[c].str.len().median() <= 3 and df[c].nunique() >= 5]
    for c in df.columns:
        for s in short:
            if s == c:
                continue
            domain = set(df[s][df[s].ne("")])
            last = df[c].str.rsplit(" ", n=1).str[-1]
            hit = df[c].str.contains(" ") & last.isin(domain) & df[s].eq("")
            if hit.any():
                out |= {(int(i), c) for i in df.index[hit]}
    return out


def entity_conflicts(df: pd.DataFrame, key: str) -> set[tuple[int, str]]:
    """When several sources describe the same entity, flag values that disagree with the majority.

    Needs one piece of configuration, the entity key (for example a flight number).
    """
    out = set()
    for _, idx in df.groupby(key, sort=False).indices.items():
        if len(idx) < 3:
            continue
        for c in df.columns:
            if c == key:
                continue
            col = df[c].to_numpy()[idx]
            top, k = Counter(col).most_common(1)[0]
            if k / len(idx) > 0.5:
                out |= {(int(df.index[i]), c) for i, v in zip(idx, col) if v != top}
    return out


def detect_cells(df: pd.DataFrame, entity_key: str | None = None) -> dict[str, set[tuple[int, str]]]:
    res = {"format": format_outliers(df), "dependency": dependency_violations(df),
           "missing": missing_values(df), "units": unit_noise(df), "leaks": token_leaks(df)}
    if entity_key:
        res["entity"] = entity_conflicts(df, entity_key)
    res["all"] = set().union(*res.values())
    return res


def prf(pred: set, truth: set) -> dict:
    tp = len(pred & truth)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(truth) if truth else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "flagged": len(pred)}
