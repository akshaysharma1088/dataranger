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


# ---- #113: the hash total has to decide the day, not just move the gap ----

def _day(rows: int, seed: int = 0) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(seed)
    return pd.DataFrame({"txn_id": [f"t{i}" for i in range(rows)], "customer_id": rng.integers(1, 50, rows),
                         "channel": rng.choice(["card", "ach"], rows), "category": "x",
                         "amount": rng.integers(100, 99_999, rows) / 100})


def _totals(day: pd.DataFrame, d, **extra) -> pd.DataFrame:
    rows = [{"txn_date": d, "channel": "*", "n": len(day), "amount": day["amount"].sum(), **extra}]
    rows += [{"txn_date": d, "channel": ch, "n": len(g), "amount": g["amount"].sum()} for ch, g in day.groupby("channel")]
    return pd.DataFrame(rows)


def _swap_in_one_channel(day: pd.DataFrame) -> pd.DataFrame:
    i, j = day.index[day["channel"] == "card"][:2]
    out = day.copy()
    out.loc[[i, j], "amount"] = out.loc[[j, i], "amount"].to_numpy()
    assert out.loc[i, "amount"] != day.loc[i, "amount"]
    return out


def test_a_swap_is_a_full_gap_at_realistic_day_sizes():
    from dataranger.patrol import DEFAULT_LAMBDA

    d = date(2026, 1, 1)
    for rows in (300, 10_000):
        day = _day(rows)
        totals = _totals(day, d, hash_total=hash_total(day))
        swapped = _swap_in_one_channel(day)
        assert residual(day, totals, d) < 1e-9
        assert residual(swapped, _totals(day, d), d) < 1e-9  # counts and sums cannot see it
        assert residual(swapped, totals, d) == 1.0 > DEFAULT_LAMBDA  # before #113 this was about 1 / rows


def test_with_rows_missing_the_hash_total_is_still_compared_as_a_fraction():
    d = date(2026, 1, 1)
    day = _day(1_000)
    totals = _totals(day, d, hash_total=hash_total(day))
    short = day.iloc[:-3]  # three late rows: count and amount are off, so an exact hash match is impossible
    gap = residual(short, totals, d)
    assert 0.0 < gap < 0.02


def test_float_rounding_of_a_large_hash_total_is_not_a_mismatch():
    d = date(2026, 1, 1)
    day = _day(300)
    stored = float(f"{hash_total(day):.15e}")  # a control-totals table that keeps 16 significant digits
    assert residual(day, _totals(day, d, hash_total=stored), d) < 1e-9


def test_a_patrol_escalates_a_swapped_day_whose_totals_are_settled():
    """Before #113 every one of these days was reported "no action needed". On a swapped day whose control
    totals are also short of late rows the swap still cannot be seen: one hash total per day cannot tell
    the two apart."""
    from dataranger.patrol import Mapping, patrol

    # a fixed start date: the generated days, and so which swapped days have settled totals, never change
    case = inject(generate(3, "small", start=date(2026, 9, 1)), mix={"swapped_amounts": 6}, extra_measures=True)
    swapped = {i.partition for i in case.issues if i.kind == "swapped_amounts" and i.detail.get("rows_affected")}
    t, man = case.warehouse["transactions"], case.manifest
    whole = man[man["channel"] == "*"].set_index("txn_date")["n"]
    settled = {p for p in swapped if (t["txn_date"] == date.fromisoformat(p)).sum() == whole[date.fromisoformat(p)]}
    cols = ["txn_id", "customer_id", "txn_date", "channel", "category", "amount"]
    res = patrol(t[cols], case.warehouse["customers"], man, Mapping(category="category"))
    decisions = {str(r["partition"]): r["decision"] for r in res.incidents}
    assert len(swapped) == 6 and len(settled) >= 3
    assert {decisions.get(p) for p in settled} == {"escalate"}
    # The other swapped days are short of late rows. Whether those escalate depends only on how many rows are
    # late, not on the swap, so nothing is asserted about them here; the rule for that case is pinned by
    # test_with_rows_missing_the_hash_total_is_still_compared_as_a_fraction.


def test_distinct_measure_follows_the_users_column_name():
    from dataranger.patrol import Mapping

    mapping = Mapping(key="payment_id", reference="CUST_ID", date="paid_on", segment="rail", amount="amount")
    totals = pd.DataFrame([{"paid_on": date(2026, 1, 1), "rail": "*", "n": 2, "amount": 30.0, "DISTINCT_cust_id": 2}])
    man = mapping.totals_to_internal(totals)
    assert "distinct_customer_id" in man.columns and list(man.columns[:2]) == ["txn_date", "channel"]
    part = pd.DataFrame({"txn_id": [1, 2], "customer_id": [7, 7], "channel": ["card", "card"],
                         "category": ["x", "x"], "amount": [10.0, 20.0]})
    assert residual(part, man, date(2026, 1, 1), "partition") == 0.5  # one customer where the source saw two
