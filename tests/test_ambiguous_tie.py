"""The gate escalates when two repair candidates reconcile equally well but disagree on the data
(issue #91), instead of silently picking one.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from dataranger.bench import generate
from dataranger.bench.inject import Case
from dataranger.bench.inject import make_manifest
from dataranger.patrol import Mapping, patrol
from dataranger.repair.agent import run
from dataranger.repair.gate import select


def _duplicate_key_tie_case():
    """A real duplicate-key incident where the two copies agree on amount (so either dedupe choice
    reconciles exactly against the source's control totals) but disagree on category (so the two
    choices leave the table in genuinely different states)."""
    inst = generate(3, "small", start=date.today() - timedelta(days=60))
    source = inst.clean_warehouse()
    warehouse = {k: v.copy() for k, v in source.items()}
    t = warehouse["transactions"]
    d = inst.days[-1]
    row = t[t["txn_date"] == d].iloc[[0]].copy()
    dup = row.copy()
    other_cat = next(c for c in t["category"].unique() if c != row.iloc[0]["category"])
    dup["category"] = other_cat  # same txn_id, same amount, different category: the ambiguity
    warehouse["transactions"] = pd.concat([t, dup], ignore_index=True)
    manifest = make_manifest(source["transactions"], __import__("numpy").random.default_rng(1), p_stale=0.0)
    return Case(instance=inst, source=source, warehouse=warehouse, issues=[], manifest=manifest), d


def test_gate_select_flags_an_ambiguous_tie_directly():
    tied_cat = [{"residual": 0.0, "changed": 1, "action": "dedupe", "tier": "auto", "invariants": True,
                "table_fingerprint": "fp_keep_first"},
                {"residual": 0.0, "changed": 1, "action": "dedupe", "tier": "auto", "invariants": True,
                "table_fingerprint": "fp_keep_last"}]
    rec = {"table_level": False, "candidates": tied_cat}
    c = select(rec)
    assert c is not None and c.get("ambiguous_tie") is True
    assert {x["table_fingerprint"] for x in c["tie_candidates"]} == {"fp_keep_first", "fp_keep_last"}


def test_gate_select_does_not_flag_a_tie_that_is_really_the_same_data():
    identical = [{"residual": 0.0, "changed": 1, "action": "dedupe", "tier": "auto", "invariants": True,
                 "table_fingerprint": "same"},
                 {"residual": 0.0, "changed": 0, "action": "none", "tier": "auto", "invariants": True,
                 "table_fingerprint": "same"}]
    rec = {"table_level": False, "candidates": identical}
    c = select(rec)
    assert not c.get("ambiguous_tie")


def test_duplicate_key_candidates_reconcile_equally_but_differ_in_data():
    case, d = _duplicate_key_tie_case()
    recs = run(case, reconcile=True)
    rec = next(r for r in recs if r["partition"] == d.isoformat())
    dedupes = [c for c in rec["candidates"] if c["action"] == "dedupe" and c["invariants"]]
    assert len(dedupes) >= 2
    residuals = {round(c["residual"], 9) for c in dedupes}
    assert residuals == {0.0}  # both choices reconcile perfectly against the source's control totals
    fingerprints = {c["table_fingerprint"] for c in dedupes}
    assert len(fingerprints) > 1  # ...but they leave the table in different states


def test_gate_escalates_the_ambiguous_duplicate_key_tie_instead_of_choosing():
    case, d = _duplicate_key_tie_case()
    recs = run(case, reconcile=True)
    rec = next(r for r in recs if r["partition"] == d.isoformat())
    chosen = select(rec)
    assert chosen is not None
    assert chosen.get("ambiguous_tie") is True
    assert len(chosen["tie_candidates"]) >= 2


def test_patrol_reports_escalate_not_auto_fixed_for_an_ambiguous_tie():
    case, d = _duplicate_key_tie_case()
    res = patrol(case.warehouse["transactions"], case.source["customers"], case.manifest, Mapping(category="category"),
                 source_facts=case.source["transactions"])
    rec = next(r for r in res.incidents if r["partition"] == d.isoformat())
    assert rec["decision"] == "escalate"
    # the ambiguous candidate is still attached (with its tie_candidates) so a dashboard can show
    # the competing repairs side by side -- it just isn't auto-applied.
    assert rec["chosen"]["ambiguous_tie"] is True
    assert "applied" not in rec


def test_allow_ambiguous_autofix_flag_restores_the_old_pick_one_behaviour():
    case, d = _duplicate_key_tie_case()
    res = patrol(case.warehouse["transactions"], case.source["customers"], case.manifest, Mapping(category="category"),
                 source_facts=case.source["transactions"], detect_ties=False)
    rec = next(r for r in res.incidents if r["partition"] == d.isoformat())
    assert rec["decision"] != "still open"
