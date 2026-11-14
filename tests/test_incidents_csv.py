import json
import re

import pandas as pd

from dataranger.cli import main


def test_demo_writes_incidents_csv_matching_audit_and_summary(tmp_path, capsys):
    out = tmp_path / "demo"
    assert main(["demo", "--out", str(out)]) == 0
    summary = capsys.readouterr().out
    total, fixed, left_alone, escalate = map(int, re.search(
        r"(\d+) incidents: (\d+) fixed, (\d+) checked and left alone, (\d+) for you", summary).groups())

    audit = [json.loads(line) for line in (out / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    incidents = pd.read_csv(out / "incidents.csv", keep_default_na=False)

    assert list(incidents.columns) == ["partition", "problems", "decision", "action", "tier", "residual"]
    assert len(incidents) == len(audit) == total
    counts = incidents["decision"].value_counts()
    assert counts.get("auto-fixed", 0) == fixed
    assert counts.get("no action needed", 0) == left_alone
    assert counts.get("escalate", 0) == escalate
    assert (incidents["partition"] == "(whole table)").sum() == sum(r["table_level"] for r in audit)
    assert incidents["partition"].ne("").all()
