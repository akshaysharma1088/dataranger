"""Score the LLM proposer's fixes under the same verification and gate as the templates.

Reads the responses committed by the Actions workflow (git branch llm-results), regenerates each
case (deterministic), verifies every LLM fix against the source totals and invariants, and compares:
  llm_raw       apply the LLM's fix as given (an agent with no checks)
  llm_monitors  apply it only if the team's monitors go quiet afterwards
  llm_gate      apply it only if it passes the calibrated gate (LTT on half the seeds)
  templates     template candidates through the calibrated gate (the earlier result)
  llm+templates the union of both proposers through the calibrated gate
Usage: python benchmarks/eval_llm.py <runs/<id>/llm folder>
Writes results/llm_summary.json.
"""
import json
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from dataranger.bench import generate, inject
from dataranger.repair.agent import run
from dataranger.repair.gate import calibrate, hb_pvalue, GRID
from dataranger.repair.llm import parse

root = Path(sys.argv[1])
DELTA, SPLITS = 0.05, 50


def load(model_dir):
    rows = [json.loads(l) for f in sorted(model_dir.glob("shard_*.jsonl")) for l in open(f)]
    by_seed = defaultdict(dict)
    for r in rows:
        fx, _ = parse(r["response"])
        by_seed[(r["size"], r["seed"])][r["partition"]] = fx
    return rows, by_seed


def one(args):
    (size, seed), extra = args
    recs = run(inject(generate(seed, size)), extra=extra)
    for r in recs:
        r.update({"size": size, "seed": seed})
    return recs


def llm_choice(rec):
    if rec["llm"] is None:
        return None
    return next((c for c in rec["candidates"] if c["fix"] == rec["llm"]), None)  # None if the fix could not run


def decide(rec, policy, lam=None):
    if rec["table_level"]:
        return None
    if policy == "llm_raw":
        return llm_choice(rec)
    if policy == "llm_monitors":
        c = llm_choice(rec)
        return c if c is not None and c["monitors_quiet"] else None
    if policy == "llm_gate":
        c = llm_choice(rec)
        return c if c is not None and c["tier"] == "auto" and c["residual"] <= lam else None
    pool = [c for c in rec["candidates"] if (policy == "templates" and "template" in c["source"]) or policy == "llm+templates"]
    if not pool:
        return None
    c = min(pool, key=lambda c: (round(c["residual"], 9), c["changed"], c["action"] != "none"))
    return c if c["tier"] == "auto" and c["residual"] <= lam else None


def stats(recs, policy, lam=None):
    inc = [r for r in recs if not r["table_level"]]
    a = [decide(r, policy, lam) for r in inc]
    real = [any(k not in ("false_alarm", "volume_spike", "big_ticket_day") for k in r["kinds"]) for r in inc]
    return {"auto": float(np.mean([x is not None for x in a])),
            "harm": float(np.mean([x is not None and not x["correct"] for x in a])),
            "fixed": float(sum(x is not None and x["correct"] and y for x, y in zip(a, real)) / max(sum(real), 1))}


def ltt(recs, policy, alpha):
    inc = [r for r in recs if not r["table_level"]]
    best = -1.0
    for lam in GRID:
        risk = np.mean([(x := decide(r, policy, lam)) is not None and not x["correct"] for r in inc])
        if hb_pvalue(float(risk), len(inc), alpha) <= DELTA:
            best = float(lam)
        else:
            break
    return best


if __name__ == "__main__":
    out = {}
    for model_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        rows, by_seed = load(model_dir)
        with Pool() as pool:
            recs = [r for rs in pool.map(one, list(by_seed.items())) for r in rs]
        inc = [r for r in recs if not r["table_level"]]
        actions = defaultdict(int)
        for r in inc:
            actions[r["llm"].split("(")[0] if r["llm"] else "escalate"] += 1
        res = {"incidents": len(inc), "responses": len(rows),
               "seconds_per_call": float(np.median([r["seconds"] for r in rows])),
               "unparseable": int(sum(parse(r["response"])[0] is None and "escalate" not in r["response"] for r in rows)),
               "llm_actions": dict(actions),
               "llm_choice_correct": float(np.mean([bool((c := llm_choice(r)) and c["correct"]) for r in inc])),
               "llm_raw": stats(inc, "llm_raw"), "llm_monitors": stats(inc, "llm_monitors")}
        keys = sorted({(r["size"], r["seed"]) for r in recs})
        rng = np.random.default_rng(0)
        for alpha in (0.01, 0.05):
            for pol in ("llm_gate", "templates", "llm+templates"):
                vals = []
                for _ in range(SPLITS):
                    perm = rng.permutation(len(keys))
                    ck = {keys[i] for i in perm[: len(keys) // 2]}
                    cal = [r for r in inc if (r["size"], r["seed"]) in ck]
                    test = [r for r in inc if (r["size"], r["seed"]) not in ck]
                    lam = ltt(cal, pol, alpha)
                    vals.append(stats(test, pol, lam) | {"lam": lam})
                res[f"{pol}|{alpha}"] = {k: float(np.mean([v[k] for v in vals])) for k in vals[0]} | \
                    {"violations": float(np.mean([v["harm"] > alpha for v in vals]))}
        # Self-checking agents: the proposal is applied when its own gap to the control totals is under a
        # hand-set tolerance, as an engineer would configure it. Same checks as the gate, no calibration.
        for tol in (0.001, 0.005, 0.01, 0.02, 0.05):
            for pol, name in (("llm_gate", "llm_selfcheck"), ("llm+templates", "llm+templates_selfcheck")):
                vals = []
                rng2 = np.random.default_rng(0)
                for _ in range(SPLITS):
                    perm = rng2.permutation(len(keys))
                    ck = {keys[i] for i in perm[: len(keys) // 2]}
                    vals.append(stats([r for r in inc if (r["size"], r["seed"]) not in ck], pol, tol))
                res[f"{name}|{tol}"] = {k: float(np.mean([v[k] for v in vals])) for k in vals[0]} | \
                    {"violates_0.01": float(np.mean([v["harm"] > 0.01 for v in vals])),
                     "violates_0.05": float(np.mean([v["harm"] > 0.05 for v in vals]))}
        out[model_dir.name] = res
        print(f"\n{model_dir.name}: {res['incidents']} incidents, {res['seconds_per_call']:.0f}s per call, "
              f"LLM fix correct {res['llm_choice_correct']:.3f}, actions {res['llm_actions']}")
        for k, v in res.items():
            if isinstance(v, dict) and "harm" in v:
                print(f"  {k:22s} auto {v['auto']:.3f} harm {v['harm']:.4f} fixed {v['fixed']:.3f}"
                      + (f" violations {v['violations']:.2f}" if "violations" in v else "")
                      + (f" P(>1%) {v['violates_0.01']:.2f} P(>5%) {v['violates_0.05']:.2f}" if "violates_0.01" in v else ""))
    json.dump(out, open("results/llm_summary.json", "w"), indent=1)
