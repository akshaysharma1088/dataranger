# Contributing

Thanks for helping. DataRanger aims to stay small, honest about risk and easy to run on a schedule.

## Set up

```bash
git clone https://github.com/akshaysharma1088/dataranger && cd dataranger
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" ruff
ruff check .
pytest -q
dataranger demo --out demo-report   # open demo-report/dashboard.html
```

## Good first contributions

Issues labelled [good first issue](https://github.com/akshaysharma1088/dataranger/labels/good%20first%20issue)
are small and well described, with the files to look at and a clear "done when" list. Comment on one
to claim it, so two people don't work on the same thing.

Other welcome contributions:

- A detector for a kind of data incident you have seen in production. Add it under
  `src/dataranger/bench/`, with a test on a tiny table.
- A candidate fix, as long as it can be checked against the source's control totals before it is applied.
- Loaders and integrations: new input formats, or helpers for dbt, Airflow or a scheduled CI job.

## Guidelines

- Never modify the user's input tables. Fixes go to a new version.
- A fix is applied automatically only when it is verified against evidence and passes the calibrated gate.
  Anything else is escalated to a person. Please keep it that way.
- LLM features stay optional. Results must not depend on a paid API.
- Every change needs a test, ideally on a small DataFrame that shows the problem.
- Code must pass `ruff check .` (the rules are in `pyproject.toml`); CI fails on new warnings.
- Keep messages and reports in plain language: what broke, what was done, and why.

## Reporting a problem

Open an issue with the command you ran, the output, and if possible small CSV files that reproduce it.
Security problems go through the private process in [SECURITY.md](SECURITY.md), not a public issue.

By taking part you agree to the [code of conduct](CODE_OF_CONDUCT.md).
