"""Conformal data cleaning with the authors' own cleaner (github.com/se-jaeger/conformal-data-cleaning, ConformalAutoGluonCleaner),
on the exact training rows run_cells2.py uses. Output: proposals with the point prediction's probability, in run_cells2's cache format.
Adaptations forced by our data or environment, all reported in the paper:
- the reference code accepts only categorical (at most 20% distinct values) or numeric columns, so columns above that cardinality
  are neither cleaned nor used as features, nor are columns with fewer than two values seen 15 times (AutoGluon drops
  classes seen fewer than 10 times, and its own holdout split needs more);
- model families RF and XT (the reference list adds FASTAI), 10 random-search trials per column (the reference experiments use 50);
- calibration on a quarter of the training rows (the reference takes 1,000 rows, more than our training sets hold);
- confidence level 0.999, the highest the reference experiments use; empty prediction sets are inliers, as in the reference script."""
import json, sys, shutil
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "cdc")
from conformal_data_cleaning.cleaner.conformal import ConformalAutoGluonCleaner
from conformal_data_cleaning.utils import is_categorical

which = sys.argv[1]                      # "third" or "half"
D = Path("benchmarks/cdc_reference/work")
splits = json.load(open(D / "splits.json"))
out_p = D / f"cdc_ref_{which}.json"
out = json.load(open(out_p)) if out_p.exists() else {}
only = sys.argv[2] if len(sys.argv) > 2 else None        # one split per process keeps memory bounded
for key, sp in splits.items():
    if key in out or (only and key != only):
        continue
    name, s = key.split("|")
    clean = pd.read_csv(D / f"{name}_clean.csv", dtype=str, keep_default_na=False)
    dirty = pd.read_csv(D / f"{name}_dirty.csv", dtype=str, keep_default_na=False)
    train = sorted(sp[which])
    cols = [c for c in clean.columns if is_categorical(clean.loc[train, c]) and (clean.loc[train, c].value_counts() >= 15).sum() >= 2]
    tr = clean.loc[train, cols].reset_index(drop=True).astype("category")
    models = Path(f"/tmp/cdc_models/{which}/{key.replace('|', '_')}")
    cleaner = ConformalAutoGluonCleaner(confidence_level=0.999, seed=int(s))
    n_fit = int(len(tr) * (1 - min(0.25, 1000 / len(tr))))
    k_max = max(int(tr[c].nunique()) for c in cols)
    holdout = min(0.5, max(0.2, (k_max + 5) / n_fit))      # AutoGluon's stratified holdout needs one row per class
    cleaner.fit(tr, ci_ag_fit_params={"verbosity": 0, "holdout_frac": holdout, "hyperparameters": {"RF": {}, "XT": {}},
                                       "hyperparameter_tune_kwargs": {"num_trials": 10}, "ag_args_fit": {"num_cpus": int(__import__("os").environ.get("CDC_CPUS", "2"))}},
                ci_ag_predictor_params={"path_prefix": models},
                ci_calibration_size=min(0.25, 1000 / len(tr)))
    rows = [r for r in range(sp["n"]) if r not in set(train)]
    X = dirty.loc[rows, cols].reset_index(drop=True)
    for c in cols:                                            # unseen values must still be valid categories
        X[c] = pd.Categorical(X[c], categories=sorted(set(tr[c].cat.categories) | set(X[c])))
    cleaned, mask, _ = cleaner.transform(X, empty_pred_sets_are_inliers=True)
    res = []
    for c in cols:
        hit = np.flatnonzero(mask[c].to_numpy() & (cleaned[c].astype(str).to_numpy() != X[c].astype(str).to_numpy()))
        if len(hit) == 0:
            continue
        proba = cleaner.predictors_[c]._predictor.predict_proba(X.drop(columns=[c]).iloc[hit]).max(axis=1).to_numpy()
        res += [[rows[i], c, str(cleaned.at[i, c]), float(p)] for i, p in zip(hit, proba)]
    out[key] = res
    json.dump(out, open(out_p, "w"))
    shutil.rmtree(models, ignore_errors=True)
    print(key, which, "columns cleaned", len(cols), "of", len(clean.columns), "proposals", len(res), flush=True)
