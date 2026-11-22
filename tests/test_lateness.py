"""A day stays "still open" instead of broken while it is still receiving late records (#85)."""
from datetime import date

import pandas as pd

from dataranger.cli import main
from dataranger.patrol import Mapping, patrol


def _mini_case():
    customers = pd.DataFrame({"customer_id": [1, 2, 3]})
    days = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    rows = []
    for i, d in enumerate(days):
        for j in range(5):
            rows.append({"txn_id": i * 10 + j, "customer_id": (j % 3) + 1, "txn_date": d,
                        "channel": "card", "category": "x", "amount": 10.0 + j})
    facts = pd.DataFrame(rows)
    manifest = pd.DataFrame([
        {"txn_date": d, "channel": "*", "n": 5, "amount": float(facts.loc[facts["txn_date"] == d, "amount"].sum())}
        for d in days
    ])
    return facts, customers, manifest, days


def _drop_rows(facts, day, n):
    idx = facts.index[facts["txn_date"] == day][:n]
    return facts.drop(idx).reset_index(drop=True)


def test_shortfall_on_the_most_recent_day_is_an_ordinary_incident_by_default():
    facts, customers, manifest, days = _mini_case()
    late_day = days[-1]
    short = _drop_rows(facts, late_day, 2)
    res = patrol(short, customers, manifest, Mapping(category="category"))
    rec = next(r for r in res.incidents if r["partition"] == late_day.isoformat())
    assert rec["decision"] != "still open"
    assert "reconcile_gap" in rec["detectors"]


def test_shortfall_inside_the_lateness_window_is_reported_as_still_open():
    facts, customers, manifest, days = _mini_case()
    late_day = days[-1]
    short = _drop_rows(facts, late_day, 2)
    res = patrol(short, customers, manifest, Mapping(category="category"), lateness_days=2)
    rec = next(r for r in res.incidents if r["partition"] == late_day.isoformat())
    assert rec["decision"] == "still open"
    assert rec["chosen"] is None
    assert rec["context"]["gap"] > 0
    assert rec["context"]["source_rows"] == 5
    assert rec["context"]["rows"] == 3


def test_a_day_leaves_the_window_as_patrols_repeat_and_data_fills_in():
    facts, customers, manifest, days = _mini_case()
    late_day = days[-1]
    mp = Mapping(category="category")

    # First patrol: a shortfall, still inside the window -> "still open".
    res1 = patrol(_drop_rows(facts, late_day, 3), customers, manifest, mp, lateness_days=2)
    r1 = next(r for r in res1.incidents if r["partition"] == late_day.isoformat())
    assert r1["decision"] == "still open"

    # Second patrol, still short but still inside the window -> "still open" again.
    res2 = patrol(_drop_rows(facts, late_day, 1), customers, manifest, mp, lateness_days=2)
    r2 = next(r for r in res2.incidents if r["partition"] == late_day.isoformat())
    assert r2["decision"] == "still open"

    # Third patrol: the data has filled in -> the day is clean, no incident at all.
    res3 = patrol(facts, customers, manifest, mp, lateness_days=2)
    assert not any(r["partition"] == late_day.isoformat() for r in res3.incidents)


def test_a_surplus_inside_the_window_is_still_an_ordinary_incident():
    facts, customers, manifest, days = _mini_case()
    late_day = days[-1]
    extra = pd.DataFrame([{"txn_id": 999, "customer_id": 1, "txn_date": late_day, "channel": "card",
                          "category": "x", "amount": 10.0}])
    surplus = pd.concat([facts, extra], ignore_index=True)
    res = patrol(surplus, customers, manifest, Mapping(category="category"), lateness_days=2)
    rec = next(r for r in res.incidents if r["partition"] == late_day.isoformat())
    assert rec["decision"] != "still open"


def test_a_day_outside_the_window_is_an_ordinary_incident_even_with_lateness_set():
    facts, customers, manifest, days = _mini_case()
    old_day = days[0]  # 2 days before the most recent day in this patrol
    short = _drop_rows(facts, old_day, 2)
    res = patrol(short, customers, manifest, Mapping(category="category"), lateness_days=1)
    rec = next(r for r in res.incidents if r["partition"] == old_day.isoformat())
    assert rec["decision"] != "still open"


def test_cli_lateness_days_flag_and_summary_line(tmp_path, capsys):
    facts, customers, manifest, days = _mini_case()
    late_day = days[-1]
    short = _drop_rows(facts, late_day, 2)
    short.to_csv(tmp_path / "facts.csv", index=False)
    customers.to_csv(tmp_path / "customers.csv", index=False)
    manifest.to_csv(tmp_path / "manifest.csv", index=False)
    out = tmp_path / "rep"
    rc = main(["patrol", "--facts-file", str(tmp_path / "facts.csv"), "--ref-file", str(tmp_path / "customers.csv"),
              "--manifest-file", str(tmp_path / "manifest.csv"), "--category", "category",
              "--lateness-days", "2", "--out", str(out)])
    assert rc == 0
    assert "still waiting on late data" in capsys.readouterr().out
    incidents = pd.read_csv(out / "incidents.csv")
    assert (incidents.loc[incidents["partition"] == late_day.isoformat(), "decision"] == "still open").all()
