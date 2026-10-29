"""Cell repair on Hospital, Flights and Beers: the calibrated gate against Baran and conformal data cleaning.

For each of 20 random splits, half the rows are a labeled calibration set and the other half is
the test set. Every method is scored on the test rows only.

  * all-candidates : apply every candidate DataRanger proposes, no gate
  * dataranger     : the same candidates through the Learn-then-Test gate. The calibration half is
                     split again: one part fits a score (gradient boosting on the candidate's
                     evidence features), the other certifies a threshold on that score
  * baran          : Raha detection + Baran correction with 20 labeled tuples (run_baran.py output)
  * cdc            : conformal data cleaning (Jaeger et al., AISTATS 2024), reimplemented: one
                     classifier per column trained on the CLEAN calibration rows, split conformal
                     prediction sets, replace values outside the set with the point prediction

Writes results/cells_summary.json.
Usage: python benchmarks/run_cells.py <baran_results_dir>
"""
import glob
import json
from pathlib import Path
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from dataranger.bench import realdata
from dataranger.repair.cells import candidates, featurize, score
from sklearn.ensemble import HistGradientBoostingClassifier as HGB
from dataranger.repair.gate import hb_pvalue

ENTITY = {"flights": "flight"}
SPLITS, DELTA = 20, 0.05
TAUS = np.round(np.arange(1.0, -0.001, -0.01), 2)  # support thresholds, strictest first
baran_dir = sys.argv[1] if len(sys.argv) > 1 else None


def ltt_tau(cands, alpha, delta):
    """Loosest support threshold certified to keep P(applied and wrong) <= alpha within this group."""
    n, best = len(cands), None
    for tau in TAUS:
        risk = sum(c["support"] >= tau and not c["correct"] for c in cands) / max(n, 1)
        if hb_pvalue(risk, n, alpha) <= delta:
            best = tau
        else:
            break
    return best


def changes_metrics(changes, clean, dirty, rows):
    """changes: {(row, col): value} restricted to test rows."""
    rows = set(rows)
    errs = {(r, c) for r in rows for c in dirty.columns if clean.at[r, c] != dirty.at[r, c]}
    ch = {k: v for k, v in changes.items() if k[0] in rows and v != dirty.at[k[0], k[1]]}
    right = sum(clean.at[r, c] == v for (r, c), v in ch.items())
    wrong = len(ch) - right
    cells = len(rows) * dirty.shape[1]
    return {"changes": len(ch), "precision": right / len(ch) if ch else 1.0, "errors_fixed": right / max(len(errs), 1),
            "wrong_per_1000_cells": 1000 * wrong / cells, "wrong_share_of_changes": wrong / len(ch) if ch else 0.0}


def cdc(clean, dirty, train_rows, test_rows, conf=0.999, max_classes=50, seed=0):
    """Conformal data cleaning, reimplemented with sklearn. Columns are treated as categorical."""
    rng = np.random.default_rng(seed)
    tr = np.array(sorted(train_rows))
    rng.shuffle(tr)
    fit_rows, cal_rows = tr[: len(tr) * 3 // 4], tr[len(tr) * 3 // 4:]
    changes = {}
    for col in clean.columns:
        top = clean.loc[tr, col].value_counts().index[:max_classes]
        if len(top) < 2:
            continue
        others = [c for c in clean.columns if c != col]
        cats = {c: {v: i for i, v in enumerate(clean.loc[tr, c].value_counts().index[:200])} for c in others}
        enc = lambda df: np.array([[cats[c].get(v, -1) for c, v in zip(others, row)] for row in df[others].to_numpy()])
        y = clean.loc[:, col].where(clean[col].isin(top), "__other__")
        model = HistGradientBoostingClassifier(max_iter=100, random_state=seed, categorical_features=list(range(len(others))))
        Xf = enc(clean.loc[fit_rows]) + 1  # categories must be non-negative
        model.fit(Xf, y.loc[fit_rows])
        classes = list(model.classes_)
        pc = model.predict_proba(enc(clean.loc[cal_rows]) + 1)
        idx = [classes.index(v) if v in classes else None for v in y.loc[cal_rows]]
        scores = np.array([1 - pc[i, j] if j is not None else 1.0 for i, j in enumerate(idx)])
        q = np.quantile(scores, min(1.0, np.ceil((len(scores) + 1) * conf) / len(scores)), method="higher")
        test = sorted(test_rows)
        pt = model.predict_proba(enc(dirty.loc[test]) + 1)
        for k, r in enumerate(test):
            v = dirty.at[r, col]
            j = classes.index(v) if v in classes else None
            in_set = j is not None and 1 - pt[k, j] <= q
            if not in_set:
                pred = classes[int(pt[k].argmax())]
                if pred != "__other__" and pred != v:
                    changes[(r, col)] = pred
    return changes


out = {}
cdc_cache = json.load(open("results/cdc_changes.json")) if Path("results/cdc_changes.json").exists() else {}
for name in realdata.DATASETS:
    case = realdata.load(name)
    clean, dirty = case.clean, case.dirty
    cands = score(candidates(dirty, ENTITY.get(name)), clean, dirty)
    baran = []
    if baran_dir:
        for f in sorted(glob.glob(f"{baran_dir}/{name}_20_*.json")):
            b = json.load(open(f))
            baran.append({(int(k.split("|")[0]), int(k.split("|")[1])): v for k, v in b["corrections"].items()})
    rng = np.random.default_rng(1)
    res = defaultdict(list)
    for s in range(SPLITS):
        perm = rng.permutation(len(dirty))
        cal_rows, test_rows = set(perm[: len(perm) // 2].tolist()), set(perm[len(perm) // 2:].tolist())
        cal = [c for c in cands if c["row"] in cal_rows]
        test = [c for c in cands if c["row"] in test_rows]
        res["all_candidates"].append(changes_metrics({(c["row"], c["col"]): c["value"] for c in test}, clean, dirty, test_rows))
        cal_list = sorted(cal_rows)
        learn_rows = set(cal_list[: len(cal_list) // 2])
        learn = [c for c in cal if c["row"] in learn_rows]
        tune = [c for c in cal if c["row"] not in learn_rows]
        cols = list(dirty.columns)
        y = [c["correct"] for c in learn]
        if len(set(y)) > 1:
            clf = HGB(max_iter=200, random_state=s).fit(featurize(learn, cols), y)
            pr = lambda cs: clf.predict_proba(featurize(cs, cols))[:, 1] if cs else np.array([])
        else:
            pr = lambda cs: np.full(len(cs), float(y[0]) if y else 0.0)
        for c, p in zip(tune, pr(tune)):
            c["score"] = float(p)
        for c, p in zip(test, pr(test)):
            c["score"] = float(p)
        for alpha in (0.01, 0.05):
            n, tau = len(tune), None
            for t in TAUS:
                risk = sum(c["score"] >= t and not c["correct"] for c in tune) / max(n, 1)
                if hb_pvalue(risk, n, alpha) <= DELTA:
                    tau = t
                else:
                    break
            applied = [c for c in test if tau is not None and c["score"] >= tau]
            m = changes_metrics({(c["row"], c["col"]): c["value"] for c in applied}, clean, dirty, test_rows)
            m["risk"] = sum(not c["correct"] for c in applied) / max(len(test), 1)
            m["violated"] = m["risk"] > alpha
            m["taus"] = {"score": tau}
            res[f"dataranger_{alpha}"].append(m)
        if baran:
            b = baran[s % len(baran)]
            cols = list(dirty.columns)
            res["baran_20"].append(changes_metrics({(r, cols[c]): v for (r, c), v in b.items()}, clean, dirty, test_rows))
        if s < 5:
            key = f"{name}|{s}"
            if key not in cdc_cache:
                cdc_cache[key] = [[r, c, v] for (r, c), v in cdc(clean, dirty, cal_rows, test_rows, seed=s).items()]
                json.dump(cdc_cache, open("results/cdc_changes.json", "w"))
            ch = {(r, c): v for r, c, v in cdc_cache[key]}
            res["cdc_0.999"].append(changes_metrics(ch, clean, dirty, test_rows))
    out[name] = {k: {m: float(np.mean([x[m] for x in v])) for m in v[0] if m != "taus"} for k, v in res.items()}
    out[name]["candidates"] = len(cands)
    print(f"\n{name}: {len(cands)} candidates, {len(case.errors)} errors")
    for k, v in out[name].items():
        if isinstance(v, dict):
            print(f"  {k:18s} fixed {v['errors_fixed']:.3f} precision {v['precision']:.3f} "
                  f"wrong/1000 cells {v['wrong_per_1000_cells']:.2f}" + (f" violations {v['violated']:.2f}" if "violated" in v else ""))
json.dump(out, open("results/cells_summary.json", "w"), indent=1)
