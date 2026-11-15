# Conformal data cleaning with the authors' code (paper, Section 6.7)

`run_reference.py` runs `ConformalAutoGluonCleaner` from https://github.com/se-jaeger/conformal-data-cleaning on the exact
training rows that `benchmarks/run_cells2.py` gives conformal cleaning, and writes `results/cdc_ref_third.json` (trained on a
third of the labeled half, for the gate) and `results/cdc_ref_half.json` (trained on the whole labeled half, ungated).
`CDC_IMPL=reference python benchmarks/run_cells2.py results/baran` then writes `results/cells2_summary_cdcref.json`.

Environment (Python 3.9, as the reference requires):

    uv python install 3.9 && uv venv -p 3.9 cdcenv
    echo "setuptools<70" > bc.txt
    uv pip install -p cdcenv/bin/python --build-constraint bc.txt "autogluon.tabular[lightgbm,catboost,xgboost]==0.8.2" \
        "scikit-learn<1.3.0" "git+https://github.com/se-jaeger/conformal-inference@cleaning-paper"
    git clone https://github.com/se-jaeger/conformal-data-cleaning cdc
    python benchmarks/cdc_reference/dump_splits.py        # main environment: tables and training rows
    for k in hospital flights beers; do for s in 0 1 2 3 4; do
      cdcenv/bin/python benchmarks/cdc_reference/run_reference.py third "$k|$s"
      cdcenv/bin/python benchmarks/cdc_reference/run_reference.py half "$k|$s"; done; done

Run from the repository root with the reference clone at ./cdc; working files go to benchmarks/cdc_reference/work/. The adaptations to our data are listed at the top of
`run_reference.py`. One split (Hospital, split 1, whole labeled half) ran out of memory on an 8 GB machine and is missing from
`cdc_ref_half.json`; the ungated Hospital row averages the other four.
