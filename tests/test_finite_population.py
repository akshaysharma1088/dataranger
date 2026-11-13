"""The exact finite-population certificate for cell repair (paper, Proposition 6.1), checked by simulation."""
import numpy as np
import pytest

from dataranger.repair.cells import certify_remainder, remainder_cutoff


def test_cutoff_matches_hand_computation():
    # 750 rows, 250 sampled, 1% target: k = 5 harmed rows allowed among the other 500. Zero harmed rows in the sample
    # still leave a C(500,6)/C(750,6) = 8.7% chance of six in the remainder, above delta = 5%: nothing certifies.
    assert remainder_cutoff(750, 250, 0.01, 0.05) == -1
    assert remainder_cutoff(750, 250, 0.01, 0.10) == 0
    assert remainder_cutoff(750, 250, 0.05, 0.05) >= 1
    # when the allowance covers the whole remainder, any sample certifies
    assert remainder_cutoff(100, 99, 1.0, 0.05) == 99


@pytest.mark.parametrize("pop_n,n,alpha", [(750, 250, 0.05), (1438, 250, 0.05), (600, 100, 0.05), (1523, 508, 0.01)])
def test_failure_rate_is_at_most_delta(pop_n, n, alpha):
    """Rows are harmed in a fixed order as the threshold falls. For every number K of rows that are ever harmed, the
    chance that the certified threshold harms more than alpha of the unsampled rows must not exceed delta."""
    delta, trials = 0.05, 4000
    rng = np.random.default_rng(7)
    c = remainder_cutoff(pop_n, n, alpha, delta)
    k = int(np.floor(alpha * (pop_n - n)))
    assert c >= 0
    for K in sorted({k + 1, k + 2, k + c + 1, k + c + 2, k + 2 * c + 3, pop_n // 2}):
        fails = 0
        for _ in range(trials):
            sampled = np.zeros(pop_n, bool)
            sampled[rng.choice(pop_n, n, replace=False)] = True
            # proposal i is wrong on row i for i < K, with score decreasing in i: row i is harmed once the threshold is below its score
            scores = -np.arange(K, dtype=float)
            t = certify_remainder(scores, np.ones(K, bool), np.arange(K), np.flatnonzero(sampled), alpha, pop_n, delta)
            harmed_unsampled = int((~sampled[:K] & (scores > t)).sum())
            fails += harmed_unsampled > k
        # allow three standard errors of Monte Carlo noise above delta
        assert fails / trials <= delta + 3 * np.sqrt(delta * (1 - delta) / trials), (K, fails / trials)


def test_threshold_applies_nothing_wrong_when_sample_is_clean():
    scores = np.array([0.9, 0.8, 0.7, 0.6])
    wrong = np.array([False, False, False, True])
    rows = np.array([0, 1, 2, 3])
    # the one wrong proposal sits on an unsampled row: the sample is clean and everything is applied
    assert certify_remainder(scores, wrong, rows, range(100, 400), 0.05, 1000, 0.05) == -np.inf
    # too small a sample certifies nothing
    assert certify_remainder(scores, wrong, rows, range(100, 110), 0.05, 1000, 0.05) is None
