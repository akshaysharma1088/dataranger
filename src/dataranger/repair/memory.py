"""Repair memory: what the agent has learned from past incidents, and how it keeps the bound.

Three mechanisms:
  * Signatures. An incident is summarized by what fired, the fix the gate would choose,
    whether a reload is possible, and the size of the remaining gap.
  * Novelty guard. A signature seen fewer than `min_seen` times before goes to a person,
    because the calibration has no evidence about it.
  * Blocklist. A signature whose remembered fixes were wrong more often than `max_wrong`
    of the time is never auto-applied again (with max_wrong = 0, one wrong fix blocks it).
The threshold itself is recalibrated each epoch on a sliding window of labeled incidents.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .gate import GRID, hb_pvalue, select


def signature(rec: dict) -> tuple:
    c = select(rec)
    if c is None:
        return ("table",)
    r = c["residual"]
    bucket = "inf" if math.isinf(r) else ("0" if r <= 1e-9 else str(int(math.floor(math.log10(r)))))
    return (tuple(rec.get("detectors", [])), c["fix"].split("(")[0] if c["action"] != "rescale" else c["fix"],
            rec.get("reload_ok"), bucket)


class Memory:
    def __init__(self, min_seen: int = 3, max_wrong: float = 0.0):
        self.seen: dict[tuple, int] = defaultdict(int)
        self.wrong: dict[tuple, int] = defaultdict(int)
        self.min_seen = min_seen
        self.max_wrong = max_wrong

    @property
    def blocked(self) -> set[tuple]:
        return {s for s, w in self.wrong.items() if w / self.seen[s] > self.max_wrong}

    def snapshot(self) -> "Memory":
        m = Memory(self.min_seen, self.max_wrong)
        m.seen, m.wrong = defaultdict(int, self.seen), defaultdict(int, self.wrong)
        return m

    def learn(self, records: list[dict]) -> None:
        """Add labeled incidents (human decisions and audited auto-fixes) to memory."""
        for r in records:
            if r["table_level"]:
                continue
            s = signature(r)
            self.seen[s] += 1
            c = select(r)
            if c is not None and not c["correct"]:
                self.wrong[s] += 1

    def allows(self, rec: dict) -> bool:
        s = signature(rec)
        n = self.seen.get(s, 0)
        return n >= self.min_seen and self.wrong.get(s, 0) / max(n, 1) <= self.max_wrong


def decide(rec: dict, lam: float, memory: Memory | None) -> dict | None:
    if rec["table_level"]:
        return None
    c = select(rec)
    if c is None or c["tier"] != "auto" or c["residual"] > lam:
        return None
    if memory is not None and not memory.allows(rec):
        return None
    return c


def calibrate(records: list[dict], memories: list[Memory | None], alpha: float, delta: float, grid=GRID) -> float:
    """Learn-then-Test for the memory policy. Each record is judged with the memory state it would have met."""
    pairs = [(r, m) for r, m in zip(records, memories) if not r["table_level"]]
    n = len(pairs)
    best = -1.0
    for lam in grid:
        risk = sum(int((a := decide(r, lam, m)) is not None and not a["correct"]) for r, m in pairs) / max(n, 1)
        if hb_pvalue(risk, n, alpha) <= delta:
            best = float(lam)
        else:
            break
    return best
