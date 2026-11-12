"""Cell repair, second round (Section 6.7): matched budgets and the gate on other systems' proposals.

Same datasets and the same 20 row splits as run_cells.py. Three questions:
  1. Labels versus budget. The gate calibrated on 20, 50, 100, 200, 500 labeled rows and on the full
     calibration half; Baran with 20, 50, 100 and 200 labeled tuples (benchmarks/run_baran.py output).
  2. Other proposers through the gate. Baran's and conformal cleaning's proposed changes, each judged
     by the table's own evidence (do the dependencies agree with the proposed value?) and certified with
     the same Learn-then-Test gate, so the comparison holds the proposals fixed and varies only the gate.
  3. Same proposals, same labels, different score. Conformal cleaning's own confidence (one minus the
     point prediction's probability) against the table evidence, each through the same gate.
Writes results/cells2_summary.json. Usage: python benchmarks/run_cells2.py <baran_results_dir>
"""
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as HGB

from dataranger.bench import realdata
from dataranger.repair.cells import candidates, score
from dataranger.repair.gate import hb_pvalue

ENTITY = {"flights": "flight"}
SPLITS, DELTA = 20, 0.05
TAUS = np.round(np.arange(1.0, -0.001, -0.01), 2)
BUDGETS = (20, 50, 100, 200, 500)
baran_dir = sys.argv[1] if len(sys.argv) > 1 else "results/baran"
SOURCES = ["dep", "entity", "units", "null_token", "leak"]


def metrics(changes, clean, dirty, rows):
    rows = set(rows)
    errs = {(r, c) for r in rows for c in dirty.columns if clean.at[r, c] != dirty.at[r, c]}
    ch = {k: v for k, v in changes.items() if k[0] in rows and v != dirty.at[k[0], k[1]]}
    right = sum(clean.at[r, c] == v for (r, c), v in ch.items())
    wrong = len(ch) - right
    cells = len(rows) * dirty.shape[1]
    return {"changes": len(ch), "precision": right / len(ch) if ch else 1.0, "errors_fixed": right / max(len(errs), 1),
            "wrong_per_1000_cells": 1000 * wrong / cells}


def evidence_features(props, ours, columns, extra=None):
    """Table evidence for arbitrary proposals {(row, col): value}: what DataRanger's own candidate for that
    cell says about the proposed value. `extra` adds a proposer-side score per proposal."""
    X = []
    for (r, c), v in props.items():
        o = ours.get((r, c))
        agree = float(o is not None and o["value"] == v)
        conflict = float(o is not None and o["value"] != v)
        sup = o["support"] if o else 0.0
        row = [agree, conflict, sup * agree, sup * conflict, (o["votes"] if o else 0) * agree, (o["rivals"] if o else 0) * agree,
               (o["share"] if o else 0.0) * agree] + [float(o is not None and o["source"] == s and agree) for s in SOURCES] + [columns.index(c)]
        if extra is not None:
            row.append(extra[(r, c)])
        X.append(row)
    return np.array(X, dtype=float)


def gate(X_cal, y_cal, X_test, alpha, seed, learn_share=0.5):
    """Learn a score on part of the labeled proposals, certify a threshold on the rest; return a mask over X_test."""
    n = len(y_cal)
    if n < 4 or len(set(y_cal)) < 2:
        return np.zeros(len(X_test), bool), None
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    li, ti = idx[: int(n * learn_share)], idx[int(n * learn_share):]
    if len(set(np.array(y_cal)[li])) < 2:
        return np.zeros(len(X_test), bool), None
    clf = HGB(max_iter=200, random_state=seed).fit(X_cal[li], np.array(y_cal)[li])
    p_t = clf.predict_proba(X_cal[ti])[:, 1]
    y_t = np.array(y_cal)[ti]
    tau = None
    for t in TAUS:
        risk = float(np.mean((p_t >= t) & (y_t == 0)))
        if hb_pvalue(risk, len(ti), alpha) <= DELTA:
            tau = t
        else:
            break
    if tau is None:
        return np.zeros(len(X_test), bool), None
    return clf.predict_proba(X_test)[:, 1] >= tau, tau


def direct_gate(s_cal, y_cal, s_test, alpha):
    """Learn-then-Test on a given score (higher = more confident), no learning step."""
    n = len(y_cal)
    tau = None
    for t in TAUS:
        risk = float(np.mean((s_cal >= t) & (np.array(y_cal) == 0))) if n else 1.0
        if hb_pvalue(risk, n, alpha) <= DELTA:
            tau = t
        else:
            break
    return (s_test >= tau) if tau is not None else np.zeros(len(s_test), bool), tau


def cdc_with_scores(clean, dirty, train_rows, test_rows, conf=0.999, max_classes=50, seed=0):
    """Conformal data cleaning as in run_cells.py, also returning the point prediction's probability."""
    rng = np.random.default_rng(seed)
    tr = np.array(sorted(train_rows))
    rng.shuffle(tr)
    fit_rows, cal_rows = tr[: len(tr) * 3 // 4], tr[len(tr) * 3 // 4:]
    changes, conf_of = {}, {}
    for col in clean.columns:
        top = clean.loc[tr, col].value_counts().index[:max_classes]
        if len(top) < 2:
            continue
        others = [c for c in clean.columns if c != col]
        cats = {c: {v: i for i, v in enumerate(clean.loc[tr, c].value_counts().index[:200])} for c in others}
        enc = lambda df: np.array([[cats[c].get(v, -1) for c, v in zip(others, row)] for row in df[others].to_numpy()])
        y = clean.loc[:, col].where(clean[col].isin(top), "__other__")
        model = HGB(max_iter=100, random_state=seed, categorical_features=list(range(len(others))))
        model.fit(enc(clean.loc[fit_rows]) + 1, y.loc[fit_rows])
        classes = list(model.classes_)
        pc = model.predict_proba(enc(clean.loc[cal_rows]) + 1)
        idx = [classes.index(v) if v in classes else None for v in y.loc[cal_rows]]
        scores = np.array([1 - pc[i, j] if j is not None else 1.0 for i, j in enumerate(idx)])
        q = np.quantile(scores, min(1.0, np.ceil((len(scores) + 1) * conf) / len(scores)), method="higher")
        allrows = sorted(set(train_rows) | set(test_rows))
        pt = model.predict_proba(enc(dirty.loc[allrows]) + 1)
        for k, r in enumerate(allrows):
            v = dirty.at[r, col]
            j = classes.index(v) if v in classes else None
            if not (j is not None and 1 - pt[k, j] <= q):
                pred = classes[int(pt[k].argmax())]
                if pred != "__other__" and pred != v:
                    changes[(r, col)] = pred
                    conf_of[(r, col)] = float(pt[k].max())
    return changes, conf_of


out = {}
cache_p = Path("results/cdc_changes_scored.json")
cdc_cache = json.load(open(cache_p)) if cache_p.exists() else {}
for name in realdata.DATASETS:
    case = realdata.load(name)
    clean, dirty = case.clean, case.dirty
    cols = list(dirty.columns)
    cands = score(candidates(dirty, ENTITY.get(name)), clean, dirty)
    ours = {(c["row"], c["col"]): c for c in cands}
    own_feats = lambda cs: np.array([[c["support"], c["votes"], c["rivals"], c["share"]] + [float(c["source"] == s) for s in SOURCES] + [cols.index(c["col"])] for c in cs], float)
    baran = defaultdict(list)
    for f in sorted(glob.glob(f"{baran_dir}/{name}_*_*.json")):
        b = json.load(open(f))
        baran[int(b["budget"])].append({(int(k.split("|")[0]), cols[int(k.split("|")[1])]): v for k, v in b["corrections"].items()})
    rng = np.random.default_rng(1)  # same splits as run_cells.py
    res = defaultdict(list)
    for s in range(SPLITS):
        perm = rng.permutation(len(dirty))
        cal_rows, test_rows = set(perm[: len(perm) // 2].tolist()), set(perm[len(perm) // 2:].tolist())
        cal_list = sorted(cal_rows)
        test_c = [c for c in cands if c["row"] in test_rows]
        # 1. labels versus budget, DataRanger's own candidates
        for b in BUDGETS + (len(cal_list),):
            lab = set(cal_list[:b])
            cal_c = [c for c in cands if c["row"] in lab]
            for alpha in (0.01, 0.05):
                m, tau = gate(own_feats(cal_c), [int(c["correct"]) for c in cal_c], own_feats(test_c), alpha, s) if cal_c else (np.zeros(len(test_c), bool), None)
                mm = metrics({(c["row"], c["col"]): c["value"] for c, k in zip(test_c, m) if k}, clean, dirty, test_rows)
                mm["certified"] = float(tau is not None)
                res[f"dataranger|budget={b if b in BUDGETS else 'half'}|{alpha}"].append(mm)
        # 2. Baran raw at each budget, and Baran's proposals through the gate (gate labels: the calibration half)
        for b, runs in sorted(baran.items()):
            props = runs[s % len(runs)]
            res[f"baran|budget={b}"].append(metrics(props, clean, dirty, test_rows))
            cal_p = {k: v for k, v in props.items() if k[0] in cal_rows}
            test_p = {k: v for k, v in props.items() if k[0] in test_rows}
            if cal_p and test_p:
                y = [int(clean.at[r, c] == v) for (r, c), v in cal_p.items()]
                for alpha in (0.01, 0.05):
                    m, tau = gate(evidence_features(cal_p, ours, cols), y, evidence_features(test_p, ours, cols), alpha, s)
                    mm = metrics({k: v for (k, v), keep in zip(test_p.items(), m) if keep}, clean, dirty, test_rows)
                    mm["certified"] = float(tau is not None)
                    res[f"baran+gate|budget={b}|{alpha}"].append(mm)
        # 3. conformal cleaning: raw, through the gate on its own confidence, on table evidence, on both
        if s < 5:
            key = f"{name}|{s}"
            if key not in cdc_cache:
                ch, cf = cdc_with_scores(clean, dirty, cal_rows, test_rows, seed=s)
                cdc_cache[key] = [[r, c, v, cf[(r, c)]] for (r, c), v in ch.items()]
                json.dump(cdc_cache, open(cache_p, "w"))
            props = {(r, c): v for r, c, v, _ in cdc_cache[key]}
            conf = {(r, c): p for r, c, _, p in cdc_cache[key]}
            res["cdc"].append(metrics(props, clean, dirty, test_rows))
            cal_p = {k: v for k, v in props.items() if k[0] in cal_rows}
            test_p = {k: v for k, v in props.items() if k[0] in test_rows}
            y = [int(clean.at[r, c] == v) for (r, c), v in cal_p.items()]
            for alpha in (0.01, 0.05):
                variants = {
                    "cdc+gate(confidence)": direct_gate(np.array([conf[k] for k in cal_p]), y, np.array([conf[k] for k in test_p]), alpha),
                    "cdc+gate(evidence)": gate(evidence_features(cal_p, ours, cols), y, evidence_features(test_p, ours, cols), alpha, s),
                    "cdc+gate(both)": gate(evidence_features(cal_p, ours, cols, conf), y, evidence_features(test_p, ours, cols, conf), alpha, s),
                }
                for vname, (m, tau) in variants.items():
                    mm = metrics({k: v for (k, v), keep in zip(test_p.items(), m) if keep}, clean, dirty, test_rows)
                    mm["certified"] = float(tau is not None)
                    res[f"{vname}|{alpha}"].append(mm)
    out[name] = {k: {m: float(np.mean([x[m] for x in v])) for m in v[0]} for k, v in res.items()}
    out[name]["n_splits"] = {k: len(v) for k, v in res.items()}
    print(f"\n{name}")
    for k, v in out[name].items():
        if isinstance(v, dict) and "errors_fixed" in v:
            print(f"  {k:36s} fixed {v['errors_fixed']:.3f} precision {v['precision']:.3f} wrong/1k {v['wrong_per_1000_cells']:6.2f}" + (f" certified {v['certified']:.2f}" if "certified" in v else ""))
json.dump(out, open("results/cells2_summary.json", "w"), indent=1)
