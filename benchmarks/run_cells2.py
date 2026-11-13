"""Cell repair on Hospital, Flights and Beers (Section 6.7), with the row as the unit of certification.

Protocol. Rows are split at random into a calibration half and a test half (20 splits). Nothing that is
certified ever sees the rows it is certified on:
  * proposer training rows (conformal cleaning only): the first third of the calibration half;
  * score-learning rows: the next part, where a classifier learns to score proposals from table evidence;
  * certification rows: the last part, where Learn-then-Test certifies a threshold on that score;
  * test rows: the other half, where everything is measured.
For DataRanger's own candidates and for Baran the calibration half is split in two (score learning,
certification); for conformal cleaning in three. Tuples that Raha or Baran labeled with ground truth are
removed from the certification and test rows of the Baran runs.

Unit and loss. The unit is a row, and a row is harmed if at least one change applied to it is wrong. Rows
with no proposal count, with loss 0, so the certified quantity is the share of all rows that end with a
wrong change applied, the row-level analogue of silent harm per incident. The certification rows are a
uniform random sample of the table's rows without replacement, to which Hoeffding-type bounds for
independent draws apply (Hoeffding 1963, Theorem 4); dependence between cells of one row is inside the
unit. Candidates are computed from the dirty table alone, without labels.

Three questions: labels versus budget for the gate; Baran's and conformal cleaning's proposals through the
gate; and the same proposals certified on the proposer's own confidence versus the table's evidence.
Writes results/cells2_summary.json. Usage: python benchmarks/run_cells2.py <baran_results_dir>
"""
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier as HGB

from dataranger.bench import realdata
from dataranger.repair.cells import candidates, score
from dataranger.repair.gate import hb_pvalue

ENTITY = {"flights": "flight"}
SPLITS, CDC_SPLITS, DELTA = 20, 5, 0.05
TAUS = np.round(np.arange(1.0, -0.001, -0.01), 2)
BUDGETS = (50, 100, 200, 500)
ALPHAS = (0.01, 0.05)
baran_dir = sys.argv[1] if len(sys.argv) > 1 else "results/baran"
SOURCES = ["dep", "entity", "units", "null_token", "leak"]


def metrics(changes, clean, dirty, rows):
    rows = set(rows)
    errs = {(r, c) for r in rows for c in dirty.columns if clean.at[r, c] != dirty.at[r, c]}
    ch = {k: v for k, v in changes.items() if k[0] in rows and v != dirty.at[k[0], k[1]]}
    right = sum(clean.at[r, c] == v for (r, c), v in ch.items())
    wrong = len(ch) - right
    harmed = {r for (r, c), v in ch.items() if clean.at[r, c] != v}
    return {"changes": len(ch), "precision": right / len(ch) if ch else 1.0, "errors_fixed": right / max(len(errs), 1),
            "wrong_per_1000_cells": 1000 * wrong / (len(rows) * dirty.shape[1]), "rows_harmed": len(harmed) / max(len(rows), 1)}


def evidence_features(props, ours, columns):
    """Table evidence for proposals {(row, col): value}: what the table's own dependencies say about the proposed value."""
    X = []
    for (r, c), v in props.items():
        o = ours.get((r, c))
        agree = float(o is not None and o["value"] == v)
        conflict = float(o is not None and o["value"] != v)
        sup = o["support"] if o else 0.0
        X.append([agree, conflict, sup * agree, sup * conflict, (o["votes"] if o else 0) * agree, (o["rivals"] if o else 0) * agree,
                  (o["share"] if o else 0.0) * agree] + [float(o is not None and o["source"] == s and agree) for s in SOURCES] + [columns.index(c)])
    return np.array(X, dtype=float).reshape(len(props), -1)


def certify_rows(p, wrong, rows, cert_rows, alpha):
    """Learn-then-Test with the row as the unit. p: score per proposal (higher = more confident); wrong: is the
    proposal wrong; rows: its row. The loss of a certification row is 1 if any proposal applied to it is wrong."""
    cert = sorted(cert_rows)
    pos = {r: i for i, r in enumerate(cert)}
    n, tau = len(cert), None
    idx = np.array([pos.get(r, -1) for r in rows])
    keep = idx >= 0
    for t in TAUS:
        bad = np.zeros(n, bool)
        hit = keep & (p >= t) & wrong
        bad[idx[hit]] = True
        if hb_pvalue(float(bad.mean()), n, alpha) <= DELTA:
            tau = t
        else:
            break
    return tau


def gated(props, X, learn_rows, cert_rows, test_rows, clean, dirty, alpha, seed, own_score=None):
    """Score proposals (a classifier fit on the learning rows, or the proposer's own score), certify a threshold on
    the certification rows, apply it on the test rows. Returns the metrics on the test rows."""
    keys = list(props)
    rows = np.array([k[0] for k in keys])
    wrong = np.array([clean.at[r, c] != v for (r, c), v in props.items()])
    if own_score is not None:
        p = np.array([own_score[k] for k in keys])
    else:
        li = np.array([r in learn_rows for r in rows])
        if li.sum() >= 4 and len(set(wrong[li])) == 2:
            p = HGB(max_iter=200, random_state=seed).fit(X[li], ~wrong[li]).predict_proba(X)[:, 1]
        else:
            p = np.ones(len(keys))   # nothing to learn from: the only policy on offer is "apply every proposal"
    tau = certify_rows(p, wrong, rows, cert_rows, alpha) if len(keys) else None
    applied = {k: props[k] for k, pk in zip(keys, p) if tau is not None and pk >= tau and k[0] in test_rows}
    m = metrics(applied, clean, dirty, test_rows)
    m.update({"certified": float(tau is not None), "exceeds": float(m["rows_harmed"] > alpha), "cert_rows": len(cert_rows)})
    return m


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
cache_full = json.load(open("results/cdc_changes_scored.json")) if Path("results/cdc_changes_scored.json").exists() else {}
cache_p = Path("results/cdc_changes_third.json")
cdc_cache = json.load(open(cache_p)) if cache_p.exists() else {}
for name in realdata.DATASETS:
    case = realdata.load(name)
    clean, dirty = case.clean, case.dirty
    cols = list(dirty.columns)
    cands = score(candidates(dirty, ENTITY.get(name)), clean, dirty)   # from the dirty table alone; `score` only attaches labels
    ours = {(c["row"], c["col"]): c for c in cands}
    own_props = {(c["row"], c["col"]): c["value"] for c in cands}
    own_X = np.array([[c["support"], c["votes"], c["rivals"], c["share"]] + [float(c["source"] == s) for s in SOURCES] + [cols.index(c["col"])] for c in cands], float)
    baran = defaultdict(list)
    for f in sorted(glob.glob(f"{baran_dir}/{name}_*_*.json")):
        b = json.load(open(f))
        baran[int(b["budget"])].append(({(int(k.split("|")[0]), cols[int(k.split("|")[1])]): v for k, v in b["corrections"].items()},
                                        set(b["labeled_tuples"]) | set(b.get("detection_labeled_tuples", [])), "detection_labeled_tuples" in b))
    rng = np.random.default_rng(1)  # same row splits as run_cells.py
    res = defaultdict(list)
    for s in range(SPLITS):
        perm = rng.permutation(len(dirty)).tolist()
        cal, test_rows = perm[: len(perm) // 2], set(perm[len(perm) // 2:])     # cal keeps its random order
        # 1. the gate on its own candidates, by labeling budget (half to learn the score, half to certify)
        for b in [x for x in BUDGETS if x < len(cal)] + [len(cal)]:
            learn, cert = set(cal[: b // 2]), set(cal[b // 2: b])
            for alpha in ALPHAS:
                res[f"dataranger|budget={b if b < len(cal) else 'half'}|{alpha}"].append(gated(own_props, own_X, learn, cert, test_rows, clean, dirty, alpha, s))
        # 2. Baran alone, and Baran's proposals through the gate. Tuples Baran or Raha labeled leave certification and test.
        for b, runs in sorted(baran.items()):
            props, labeled, has_det = runs[s % len(runs)]
            t_rows = test_rows - labeled
            res[f"baran|budget={b}"].append(metrics(props, clean, dirty, t_rows))
            if has_det:
                learn, cert = set(cal[: len(cal) // 2]) - labeled, set(cal[len(cal) // 2:]) - labeled
                for alpha in ALPHAS:
                    res[f"baran+gate|budget={b}|{alpha}"].append(gated(props, evidence_features(props, ours, cols), learn, cert, t_rows, clean, dirty, alpha, s))
        # 3. conformal cleaning. Alone, trained on the whole calibration half (its best case); through the gate, trained on the
        #    first third only, so that the rows that certify the gate never trained the proposer.
        if s < CDC_SPLITS:
            if f"{name}|{s}" in cache_full:
                res["cdc"].append(metrics({(r, c): v for r, c, v, _ in cache_full[f"{name}|{s}"]}, clean, dirty, test_rows))
            third = len(cal) // 3
            train, learn, cert = set(cal[:third]), set(cal[third: 2 * third]), set(cal[2 * third:])
            key = f"{name}|{s}"
            if key not in cdc_cache:
                ch, cf = cdc_with_scores(clean, dirty, train, (set(perm) - train), seed=s)
                cdc_cache[key] = [[r, c, v, cf[(r, c)]] for (r, c), v in ch.items()]
                json.dump(cdc_cache, open(cache_p, "w"))
            props = {(r, c): v for r, c, v, _ in cdc_cache[key] if r not in train}
            conf = {(r, c): p for r, c, _, p in cdc_cache[key] if r not in train}
            res["cdc_third"].append(metrics(props, clean, dirty, test_rows))
            X = evidence_features(props, ours, cols)
            for alpha in ALPHAS:
                res[f"cdc+gate(confidence)|{alpha}"].append(gated(props, X, learn, cert, test_rows, clean, dirty, alpha, s, own_score=conf))
                res[f"cdc+gate(evidence)|{alpha}"].append(gated(props, X, learn, cert, test_rows, clean, dirty, alpha, s))
        print(name, "split", s, flush=True)
    out[name] = {k: {m: float(np.mean([x[m] for x in v])) for m in v[0]} for k, v in res.items()}
    out[name]["n_splits"] = {k: len(v) for k, v in res.items()}
    out[name]["rows"] = len(dirty)
    print(f"\n{name}")
    for k, v in out[name].items():
        if isinstance(v, dict) and "errors_fixed" in v:
            print(f"  {k:34s} fixed {v['errors_fixed']:.3f} prec {v['precision']:.3f} wrong/1k {v['wrong_per_1000_cells']:6.2f} rows harmed {v['rows_harmed']:.4f}"
                  + (f" certified {v['certified']:.2f} exceeds {v['exceeds']:.2f} n={v['cert_rows']:.0f}" if "certified" in v else ""), flush=True)
    json.dump(out, open("results/cells2_summary.json", "w"), indent=1)
