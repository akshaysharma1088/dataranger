"""Ablation: the patrol loop without the reconciliation monitor.

Errors that no detector surfaces never become incidents, so they stay wrong without anyone
deciding anything. This counts them, and scores the gate at the alpha = 1% threshold.
Usage: python benchmarks/ablation_reconcile.py [n_small]   Writes results/ablation_reconcile.json.
"""
import json
import sys
from multiprocessing import Pool

import numpy as np

from dataranger.bench import generate, inject
from dataranger.repair.agent import run
from dataranger.repair.gate import evaluate

LAM = json.load(open("results/repair_summary.json"))["calibrated"]["residual|0.01"]["lambda_median"]
DECOYS = {"volume_spike", "big_ticket_day"}


def one(seed):
    case = inject(generate(seed, "small"))
    out = {}
    for rec in (True, False):
        recs = run(case, reconcile=rec)
        seen = {r["partition"] for r in recs if not r["table_level"]}
        errs = [i for i in case.issues if i.partition and not i.decoy]
        out[rec] = {"recs": recs, "unseen": sum(i.partition not in seen for i in errs), "errors": len(errs)}
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    with Pool() as p:
        res = p.map(one, range(1, n + 1))
    summary = {}
    for rec in (True, False):
        recs = [r for x in res for r in x[rec]["recs"]]
        e = evaluate(recs, "verified", LAM)
        summary["with_reconciliation" if rec else "without_reconciliation"] = e | {
            "errors_never_surfaced": float(sum(x[rec]["unseen"] for x in res) / sum(x[rec]["errors"] for x in res)),
            "lambda": LAM}
    json.dump(summary, open("results/ablation_reconcile.json", "w"), indent=1)
    for k, v in summary.items():
        print(k, {a: round(b, 4) if isinstance(b, float) else b for a, b in v.items()})
