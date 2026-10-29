"""The gate (stage 5) and the policies it is compared against.

The calibrated gate picks the candidate with the smallest residual and applies it only if
its tier allows automation and its residual is at most a threshold chosen by Learn-then-Test
(Angelopoulos et al., 2021). Guarantee: with probability at least 1 - delta over the
calibration incidents, P(an incident ends with a wrong fix applied) <= alpha on new
incidents exchangeable with them.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import binom

GRID = np.concatenate([[0.0], np.geomspace(1e-4, 0.5, 60)])


RES = "residual"  # which evidence the verified policies read: "residual" (per channel) or "residual_partition"


def select(rec: dict, res: str | None = None) -> dict | None:
    """Best-supported candidate: smallest residual; ties go to the smallest change, 'none' first."""
    res = res or RES
    cands = rec["candidates"]
    if not cands:
        return None
    return min(cands, key=lambda c: (round(c[res], 9), c["changed"], c["action"] != "none"))


def decide(rec: dict, policy: str, lam: float | None = None, res: str | None = None) -> dict | None:
    """Return the candidate the policy applies, or None to escalate."""
    if rec["table_level"]:
        return None
    cands = rec["candidates"]
    if policy == "rules":  # act on every alert with the default template, no checks
        return next((c for c in cands if c["fix"] == rec["default"]), None)
    if policy == "monitors":  # an agent whose fix only has to quiet the existing monitors
        ok = [c for c in cands if c["action"] != "none" and c["monitors_quiet"]]
        return min(ok, key=lambda c: c["changed"]) if ok else None
    if policy == "verified":  # source-checked, tier-aware, threshold lam
        res = res or RES
        c = select(rec, res)
        return c if c and c["tier"] == "auto" and c[res] <= lam else None
    raise ValueError(policy)


def loss(rec: dict, applied: dict | None) -> int:
    return int(applied is not None and not applied["correct"])


def hb_pvalue(risk_hat: float, n: int, alpha: float) -> float:
    """Hoeffding-Bentkus p-value for H0: risk > alpha (Bates et al., 2021)."""
    if risk_hat >= alpha:
        return 1.0
    a = risk_hat
    h = (a * math.log(a / alpha) if a > 0 else 0.0) + (1 - a) * math.log((1 - a) / (1 - alpha))
    hoeffding = math.exp(-n * h)
    bentkus = math.e * binom.cdf(math.ceil(n * risk_hat), n, alpha)
    return float(min(hoeffding, bentkus, 1.0))


def calibrate(records: list[dict], alpha: float = 0.05, delta: float = 0.05, grid=GRID, res: str | None = None) -> float:
    """Learn-then-Test with fixed-sequence testing from the strictest threshold outward.

    Returns the loosest threshold certified to keep silent harm at most alpha, or -1 if
    even the strictest one cannot be certified (then everything escalates).
    """
    recs = [r for r in records if not r["table_level"]]
    n = len(recs)
    best = -1.0
    for lam in grid:
        risk = np.mean([loss(r, decide(r, "verified", lam, res)) for r in recs]) if n else 1.0
        if hb_pvalue(float(risk), n, alpha) <= delta:
            best = float(lam)
        else:
            break
    return best


def calibrate_clusters(records: list[dict], cluster, alpha: float = 0.05, delta: float = 0.05, grid=GRID,
                       res: str | None = None) -> float:
    """Learn-then-Test with the cluster (a table, an estate, a period) as the unit of exchangeability.

    The loss of a cluster is the share of its incidents that end in silent harm, a number in [0, 1],
    so the Hoeffding-Bentkus p-value stays valid with n = number of clusters, however strongly the
    incidents inside a cluster depend on each other. The certified risk is the expected harm rate of
    a new cluster.
    """
    groups: dict = {}
    for r in records:
        if not r["table_level"]:
            groups.setdefault(cluster(r), []).append(r)
    n = len(groups)
    best = -1.0
    for lam in grid:
        risk = np.mean([np.mean([loss(r, decide(r, "verified", lam, res)) for r in g]) for g in groups.values()]) if n else 1.0
        if hb_pvalue(float(risk), n, alpha) <= delta:
            best = float(lam)
        else:
            break
    return best


def evaluate(records: list[dict], policy: str, lam: float | None = None, res: str | None = None) -> dict:
    recs = [r for r in records if not r["table_level"]]
    applied = [decide(r, policy, lam, res) for r in recs]
    harm = [loss(r, a) for r, a in zip(recs, applied)]
    real = [any(k not in ("false_alarm", "volume_spike", "big_ticket_day") for k in r["kinds"]) for r in recs]
    fixed_real = [a is not None and a["correct"] and x for a, x in zip(applied, real)]
    return {"incidents": len(recs), "auto_rate": float(np.mean([a is not None for a in applied])),
            "harm": float(np.mean(harm)), "harm_count": int(sum(harm)),
            "real_errors_fixed": float(np.sum(fixed_real) / max(sum(real), 1))}


def group_of(rec: dict) -> str:
    """Calibration group: 'none' decisions (declaring an alert benign) carry different risk from repairs."""
    c = select(rec)
    return "none" if c is None or c["action"] == "none" else "repair"


def calibrate_groups(records: list[dict], alpha: float = 0.05, delta: float = 0.05, grid=GRID) -> dict:
    """One Learn-then-Test threshold per group, each certified at alpha within its group.

    Silent harm overall is a mixture of the group risks, so it is also at most alpha
    (each group's bound holds with probability 1 - delta/2 when two groups are tested at delta/2).
    """
    groups: dict[str, list[dict]] = {}
    for r in records:
        if not r["table_level"]:
            groups.setdefault(group_of(r), []).append(r)
    return {g: calibrate(rs, alpha, delta / len(groups), grid) for g, rs in groups.items()}


def evaluate_groups(records: list[dict], lams: dict) -> dict:
    recs = [r for r in records if not r["table_level"]]
    applied = [decide(r, "verified", lams.get(group_of(r), -1.0)) for r in recs]
    harm = [loss(r, a) for r, a in zip(recs, applied)]
    real = [any(k not in ("false_alarm", "volume_spike", "big_ticket_day") for k in r["kinds"]) for r in recs]
    fixed_real = [a is not None and a["correct"] and x for a, x in zip(applied, real)]
    return {"incidents": len(recs), "auto_rate": float(np.mean([a is not None for a in applied])),
            "harm": float(np.mean(harm)), "harm_count": int(sum(harm)),
            "real_errors_fixed": float(np.sum(fixed_real) / max(sum(real), 1))}
