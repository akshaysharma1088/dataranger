import numpy as np
import pandas as pd
import pytest

from dataranger.bench import generate, inject
from dataranger.repair import fixes as F
from dataranger.repair.agent import run
from dataranger.repair.gate import calibrate, decide, evaluate, hb_pvalue
from dataranger.repair.verify import matches_truth, residual


@pytest.fixture(scope="module")
def records():
    return run(inject(generate(4, "small", n_days=50)))


def test_hb_pvalue_behaves():
    assert hb_pvalue(0.0, 400, 0.01) < 0.05      # 400 clean incidents certify 1%
    assert hb_pvalue(0.0, 100, 0.01) > 0.05      # 100 do not
    assert hb_pvalue(0.02, 1000, 0.01) == 1.0    # observed risk above target never certifies


def test_ltt_controls_risk_on_synthetic_losses():
    rng = np.random.default_rng(0)
    violations = 0
    for _ in range(100):
        def batch(n):  # threshold = residual; wrong fixes have higher residuals on average
            wrong = rng.random(n) < 0.1
            res = np.where(wrong, rng.uniform(0, 0.2, n), rng.uniform(0, 0.05, n))
            return [{"table_level": False, "kinds": ["x"], "candidates": [
                {"fix": "f", "action": "dedupe", "tier": "auto", "residual": r, "changed": 1, "correct": not w}]}
                for r, w in zip(res, wrong)]
        lam = calibrate(batch(500), alpha=0.05, delta=0.1)
        violations += evaluate(batch(5000), "verified", lam)["harm"] > 0.05
    assert violations / 100 <= 0.1


def test_fixes_never_modify_input():
    inst = generate(1, "small", n_days=40)
    part = inst.source_txn[inst.source_txn["txn_date"] == inst.days[5]]
    before = part.copy()
    F.apply(F.make("rescale", factor=0.01, channel="card"), part, None, inst.customers)
    pd.testing.assert_frame_equal(part, before)


def test_reload_matches_truth_and_manifest():
    c = inject(generate(2, "small", n_days=40), p_stale=0.0)
    d = c.instance.days[20]
    truth = c.source["transactions"][c.source["transactions"]["txn_date"] == d]
    fixed = F.apply(F.make("reload"), truth.iloc[:0], truth, c.source["customers"])
    assert matches_truth(fixed, truth) and residual(fixed, c.manifest, d) == 0.0


def test_verified_gate_beats_baselines_on_harm(records):
    rules = evaluate(records, "rules")
    monitors = evaluate(records, "monitors")
    verified = evaluate(records, "verified", 0.005)
    assert verified["harm"] <= monitors["harm"] <= rules["harm"]


def test_escalate_tier_is_never_auto_applied(records):
    for r in records:
        a = decide(r, "verified", 1e9)
        assert a is None or a["tier"] == "auto"
