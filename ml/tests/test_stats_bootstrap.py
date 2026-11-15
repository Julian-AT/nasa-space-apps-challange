from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from sklearn.metrics import balanced_accuracy_score
from sklearn.metrics import r2_score as sk_r2

from terraluna_ml import stats


def test_bootstrap_is_seeded_and_covers() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=200)
    blocks = np.repeat(np.arange(40), 5)
    a = stats.block_bootstrap(blocks, lambda i: float(x[i].mean()), seed=3)
    b = stats.block_bootstrap(blocks, lambda i: float(x[i].mean()), seed=3)
    c = stats.block_bootstrap(blocks, lambda i: float(x[i].mean()), seed=4)
    assert a == b
    assert a != c
    assert a.n_blocks == 40 and a.n_resamples == 1000
    assert a.ci_low < a.estimate < a.ci_high


def _mean_of(v: np.ndarray) -> Callable[[np.ndarray], float]:
    return lambda i: float(v[i].mean())


def test_coverage_band() -> None:
    hits = 0
    for s in range(100):
        r = np.random.default_rng(100 + s)
        v = r.normal(0.0, 1.0, size=40)
        iv = stats.block_bootstrap(np.arange(40), _mean_of(v), seed=s)
        hits += iv.ci_low <= 0.0 <= iv.ci_high
    assert 88 <= hits <= 99


def test_one_block_raises() -> None:
    with pytest.raises(stats.BootstrapError, match="at least 2"):
        stats.block_bootstrap(["a"] * 5, lambda i: 0.0)


def test_paired_identical_and_shifted() -> None:
    rng = np.random.default_rng(0)
    b = rng.normal(size=60)
    blocks = np.arange(60) // 3
    same = stats.paired_block_bootstrap(
        blocks, lambda i: float(b[i].mean()), lambda i: float(b[i].mean())
    )
    assert same.estimate == 0.0 and same.ci_low <= 0.0 <= same.ci_high
    a = b + 0.2
    shifted = stats.paired_block_bootstrap(
        blocks, lambda i: float(a[i].mean()), lambda i: float(b[i].mean())
    )
    assert shifted.ci_low == pytest.approx(0.2, abs=1e-12)


def test_pairing_is_narrower_than_independent() -> None:
    rng = np.random.default_rng(2)
    base = rng.normal(size=50)
    other = base + rng.normal(scale=0.05, size=50)
    blocks = np.arange(50)
    paired = stats.paired_block_bootstrap(
        blocks, lambda i: float(base[i].mean()), lambda i: float(other[i].mean())
    )
    ia = stats.block_bootstrap(blocks, lambda i: float(base[i].mean()), seed=1)
    ib = stats.block_bootstrap(blocks, lambda i: float(other[i].mean()), seed=2)
    assert (paired.ci_high - paired.ci_low) < 0.5 * (
        (ia.ci_high - ia.ci_low) + (ib.ci_high - ib.ci_low)
    )


def test_sums_paths_agree_with_row_path_and_sklearn() -> None:
    rng = np.random.default_rng(3)
    y = rng.normal(size=120)
    p = y + rng.normal(scale=0.5, size=120)
    blocks = np.arange(120) // 6
    fast = stats.block_bootstrap_sums(
        blocks, stats.r2_row_stats(y, p), stats.r2_combine, n_resamples=200, seed=5
    )
    slow = stats.block_bootstrap(
        blocks, lambda i: stats.r2_score(y[i], p[i]), n_resamples=200, seed=5
    )
    assert fast.estimate == pytest.approx(sk_r2(y, p))
    assert fast.ci_low == pytest.approx(slow.ci_low) and fast.ci_high == pytest.approx(slow.ci_high)
    z = rng.integers(0, 3, size=120)
    q = np.where(rng.random(120) < 0.7, z, rng.integers(0, 3, size=120))
    ba = stats.block_bootstrap_sums(
        blocks,
        stats.balanced_accuracy_row_stats(z, q, [0, 1, 2]),
        stats.balanced_accuracy_combine,
        n_resamples=50,
    )
    assert ba.estimate == pytest.approx(balanced_accuracy_score(z, q))
    assert stats.balanced_accuracy(z, q) == pytest.approx(ba.estimate)


def test_paired_sums_known_shift() -> None:
    rng = np.random.default_rng(4)
    y = rng.normal(size=100)
    good = y + rng.normal(scale=0.1, size=100)
    bad = y + rng.normal(scale=1.0, size=100)
    iv = stats.paired_block_bootstrap_sums(
        np.arange(100) // 5,
        stats.r2_row_stats(y, good),
        stats.r2_combine,
        stats.r2_row_stats(y, bad),
        stats.r2_combine,
    )
    assert iv.ci_low > 0


def test_jaccard_and_random_expectation() -> None:
    assert stats.jaccard_at_k([1, 2, 3], [3, 2, 1], 3) == 1.0
    assert stats.jaccard_at_k([1, 2], [3, 4], 2) == 0.0
    assert stats.jaccard_at_k([1, 2, 3, 4], [3, 4, 5, 6], 4) == pytest.approx(1 / 3)
    # n == k: the two subsets are identical.
    assert stats.random_jaccard_expectation(20, 20) == pytest.approx(1.0)
    rng = np.random.default_rng(0)
    sims = [
        stats.jaccard_at_k(rng.permutation(200)[:20].tolist(), rng.permutation(200)[:20].tolist())
        for _ in range(4000)
    ]
    assert np.mean(sims) == pytest.approx(stats.random_jaccard_expectation(200, 20), abs=0.01)


def test_spearman_and_constant() -> None:
    x = np.arange(10.0)
    assert stats.spearman(x, x**3) == pytest.approx(1.0)
    assert stats.spearman(x, -x) == pytest.approx(-1.0)
    assert np.isnan(stats.spearman(x, np.ones(10)))
    # ties use average ranks: (1,2,2,3) vs (1,2,3,4) -> Pearson of ranks (1,2.5,2.5,4), (1,2,3,4)
    assert stats.spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(
        float(np.corrcoef([1, 2.5, 2.5, 4], [1, 2, 3, 4])[0, 1])
    )
