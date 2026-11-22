"""Extra control measures catch errors that reconcile at the n/amount level (#87)."""
from datetime import date, timedelta

import pandas as pd

from dataranger.bench import generate, inject
from dataranger.bench.inject import EXPECTED
from dataranger.repair.agent import run
from dataranger.repair.verify import distinct_count, hash_total, residual


def test_hash_total_changes_when_two_amounts_are_swapped():
    df = pd.DataFrame({"txn_id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]})
    before = hash_total(df)
    swapped = df.copy()
    swapped.loc[[0, 1], "amount"] = swapped.loc[[1, 0], "amount"].to_numpy()
    after = hash_total(swapped)
    assert before != after
    # n and the aggregate sum are unchanged by the swap -- the whole point of the planted error.
    assert len(df) == len(swapped) and df["amount"].sum() == swapped["amount"].sum()


def test_hash_total_is_order_independent():
    df = pd.DataFrame({"txn_id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]})
    assert hash_total(df) == hash_total(df.iloc[::-1].reset_index(drop=True))


def test_distinct_count():
    df = pd.DataFrame({"customer_id": [1, 1, 2, 3]})
    assert distinct_count(df, "customer_id") == 3
    assert distinct_count(df, "nope") == 0


def test_residual_is_unaffected_when_the_manifest_has_no_extra_measures():
    part = pd.DataFrame({"txn_id": [1, 2], "customer_id": [1, 2], "channel": ["card", "card"],
                         "category": ["x", "x"], "amount": [10.0, 20.0]})
    manifest = pd.DataFrame([{"txn_date": date(2026, 1, 1), "channel": "*", "n": 2, "amount": 30.0}])
    assert residual(part, manifest, date(2026, 1, 1)) == 0.0


def test_residual_catches_a_swap_only_when_hash_total_is_in_the_manifest():
    d = date(2026, 1, 1)
    original = pd.DataFrame({"txn_id": [1, 2], "customer_id": [1, 2], "channel": ["card", "card"],
                             "category": ["x", "x"], "amount": [10.0, 20.0]})
    swapped = original.copy()
    swapped.loc[[0, 1], "amount"] = swapped.loc[[1, 0], "amount"].to_numpy()

    plain_manifest = pd.DataFrame([{"txn_date": d, "channel": "*", "n": 2, "amount": 30.0}])
    assert residual(swapped, plain_manifest, d) == 0.0  # n and amount alone: looks perfectly fine

    rich_manifest = pd.DataFrame([{"txn_date": d, "channel": "*", "n": 2, "amount": 30.0,
                                   "hash_total": hash_total(original), "min_amount": 10.0, "max_amount": 20.0,
                                   "distinct_customer_id": 2}])
    assert residual(original, rich_manifest, d) == 0.0  # the unswapped data still matches exactly
    assert residual(swapped, rich_manifest, d) > 0.0  # the swap is now visible


def test_min_max_amount_catch_a_shift_that_preserves_the_sum():
    d = date(2026, 1, 1)
    part = pd.DataFrame({"txn_id": [1, 2], "customer_id": [1, 2], "channel": ["card", "card"],
                         "category": ["x", "x"], "amount": [5.0, 25.0]})  # same sum (30), different min/max
    manifest = pd.DataFrame([{"txn_date": d, "channel": "*", "n": 2, "amount": 30.0,
                             "min_amount": 10.0, "max_amount": 20.0}])
    assert residual(part, manifest, d) > 0.0


def test_benchmark_swapped_amounts_case_is_defined_and_escalates():
    assert EXPECTED["swapped_amounts"] == ("reload_partition", "escalate")
    case = inject(generate(3, "small", start=date.today() - timedelta(days=60)), mix={"swapped_amounts": 2},
                  extra_measures=True)
    issues = [i for i in case.issues if i.kind == "swapped_amounts"]
    assert len(issues) == 2
    assert case.manifest["channel"].eq("*").sum() and "hash_total" in case.manifest.columns
    assert case.manifest.loc[case.manifest["channel"] == "*", "hash_total"].notna().any()


def test_without_extra_measures_the_swap_reconciles_and_is_not_flagged():
    # Documents the gap the issue describes: with only n and amount, a swap that preserves both
    # passes reconciliation silently.
    case = inject(generate(3, "small", start=date.today() - timedelta(days=60)), mix={"swapped_amounts": 2},
                  extra_measures=False)
    recs = run(case, reconcile=True)
    flagged_dates = {r["partition"] for r in recs if "reconcile_gap" in (r.get("detectors") or [])}
    swapped_dates = {i.partition for i in case.issues if i.kind == "swapped_amounts" and i.detail.get("rows_affected")}
    assert swapped_dates and not (swapped_dates & flagged_dates)


def test_with_extra_measures_the_swap_is_caught_by_reconciliation():
    case = inject(generate(3, "small", start=date.today() - timedelta(days=60)), mix={"swapped_amounts": 2},
                  extra_measures=True)
    recs = run(case, reconcile=True)
    flagged_dates = {r["partition"] for r in recs if "reconcile_gap" in (r.get("detectors") or [])}
    swapped_dates = {i.partition for i in case.issues if i.kind == "swapped_amounts" and i.detail.get("rows_affected")}
    assert swapped_dates and swapped_dates <= flagged_dates
