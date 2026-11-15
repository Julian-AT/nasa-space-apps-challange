"""Spatial-block bootstrap and ranking statistics for the PRD 11.6 evaluation tests.

Spatial blocks, never windows, are the unit of resampling (PRD 9): overlapping windows are not
independent sites. Every interval is a 95% percentile interval over ``n_resamples`` (1,000 by
default) resamples of block ids with replacement, from a seeded generator, so the same seed
gives identical output. The same seed also gives identical resampled blocks to every statistic,
which is what makes the paired bootstrap paired. Pure numpy, scipy and scikit-learn.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from sklearn.metrics import balanced_accuracy_score

N_RESAMPLES = 1000
CI_PERCENT = (2.5, 97.5)

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


class BootstrapError(ValueError):
    """A bootstrap that cannot give an honest interval."""


@dataclass(frozen=True)
class Interval:
    """A point estimate with its 95% percentile interval over spatial blocks."""

    estimate: float
    ci_low: float
    ci_high: float
    n_blocks: int
    n_resamples: int
    n_valid_resamples: int
    seed: int


def _groups(block_ids: Sequence[object] | npt.NDArray[np.generic]) -> list[I64]:
    arr = np.asarray(block_ids).astype(str)
    _, inverse = np.unique(arr, return_inverse=True)
    n_blocks = int(inverse.max()) + 1 if inverse.size else 0
    if n_blocks < 2:
        raise BootstrapError(
            f"a block bootstrap needs at least 2 spatial blocks, got {n_blocks}; "
            "a one-block interval would have zero width"
        )
    order = np.argsort(inverse, kind="stable")
    counts = np.bincount(inverse, minlength=n_blocks)
    return [np.asarray(g, dtype=np.int64) for g in np.split(order, np.cumsum(counts)[:-1])]


def _weights(n_blocks: int, n_resamples: int, seed: int) -> I64:
    """``(n_resamples, n_blocks)`` counts of how often each block is drawn."""
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_blocks, size=(n_resamples, n_blocks))
    out = np.zeros((n_resamples, n_blocks), dtype=np.int64)
    for i in range(n_resamples):
        out[i] = np.bincount(draws[i], minlength=n_blocks)
    return out


def _finish(estimate: float, values: F64, n_blocks: int, n_resamples: int, seed: int) -> Interval:
    good = values[np.isfinite(values)]
    if good.size == 0:
        raise BootstrapError("the statistic was undefined in every resample")
    lo, hi = np.percentile(good, CI_PERCENT)
    return Interval(
        estimate=float(estimate),
        ci_low=float(lo),
        ci_high=float(hi),
        n_blocks=n_blocks,
        n_resamples=n_resamples,
        n_valid_resamples=int(good.size),
        seed=seed,
    )


def _rows(groups: list[I64], weights: I64) -> I64:
    return np.concatenate([np.repeat(groups[b], w) for b, w in enumerate(weights) if w > 0])


def block_bootstrap(
    block_ids: Sequence[object] | npt.NDArray[np.generic],
    statistic: Callable[[I64], float],
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 0,
) -> Interval:
    """Interval of ``statistic(row_indices)``; row indices repeat as their block is redrawn."""
    groups = _groups(block_ids)
    n_rows = sum(len(g) for g in groups)
    estimate = statistic(np.arange(n_rows, dtype=np.int64))
    weights = _weights(len(groups), n_resamples, seed)
    values = np.asarray([statistic(_rows(groups, w)) for w in weights], dtype=np.float64)
    return _finish(estimate, values, len(groups), n_resamples, seed)


def paired_block_bootstrap(
    block_ids: Sequence[object] | npt.NDArray[np.generic],
    statistic_a: Callable[[I64], float],
    statistic_b: Callable[[I64], float],
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 0,
) -> Interval:
    """Interval of ``statistic_a - statistic_b`` with the same resampled blocks for both arms."""
    groups = _groups(block_ids)
    n_rows = sum(len(g) for g in groups)
    everything = np.arange(n_rows, dtype=np.int64)
    estimate = statistic_a(everything) - statistic_b(everything)
    weights = _weights(len(groups), n_resamples, seed)
    values = np.empty(n_resamples)
    for i, w in enumerate(weights):
        rows = _rows(groups, w)
        values[i] = statistic_a(rows) - statistic_b(rows)
    return _finish(estimate, values, len(groups), n_resamples, seed)


def _block_sums(
    block_ids: Sequence[object] | npt.NDArray[np.generic], stats: F64
) -> tuple[F64, int]:
    groups = _groups(block_ids)
    return np.stack([stats[g].sum(axis=0) for g in groups]), len(groups)


def block_bootstrap_sums(
    block_ids: Sequence[object] | npt.NDArray[np.generic],
    row_stats: F64,
    combine: Callable[[F64], float],
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 0,
) -> Interval:
    """Fast path for statistics that are functions of sums of per-row quantities (R2, accuracy).

    ``row_stats`` is ``(n_rows, m)``; ``combine`` maps the ``(m,)`` totals to the statistic.
    """
    sums, n_blocks = _block_sums(block_ids, np.asarray(row_stats, dtype=np.float64))
    weights = _weights(n_blocks, n_resamples, seed)
    totals = weights @ sums
    values = np.asarray([combine(t) for t in totals], dtype=np.float64)
    return _finish(combine(sums.sum(axis=0)), values, n_blocks, n_resamples, seed)


def paired_block_bootstrap_sums(
    block_ids: Sequence[object] | npt.NDArray[np.generic],
    stats_a: F64,
    combine_a: Callable[[F64], float],
    stats_b: F64,
    combine_b: Callable[[F64], float],
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 0,
) -> Interval:
    """Paired interval of ``combine_a(a) - combine_b(b)`` for row-aligned per-row statistics."""
    sums_a, n_blocks = _block_sums(block_ids, np.asarray(stats_a, dtype=np.float64))
    sums_b, _ = _block_sums(block_ids, np.asarray(stats_b, dtype=np.float64))
    weights = _weights(n_blocks, n_resamples, seed)
    ta, tb = weights @ sums_a, weights @ sums_b
    values = np.asarray([combine_a(x) - combine_b(y) for x, y in zip(ta, tb, strict=True)])
    estimate = combine_a(sums_a.sum(axis=0)) - combine_b(sums_b.sum(axis=0))
    return _finish(estimate, values, n_blocks, n_resamples, seed)


# --- R2 and accuracy as sums ---------------------------------------------------------------------


def r2_row_stats(y: F64, pred: F64) -> F64:
    """Per-row ``(1, y, y^2, squared error)`` so that R2 is a function of block sums."""
    return np.column_stack([np.ones_like(y), y, y * y, (y - pred) ** 2])


def r2_combine(totals: F64) -> float:
    n, sy, syy, sse = (float(t) for t in totals)
    sst = syy - sy * sy / n if n > 0 else 0.0
    if n < 2 or not sst > 1e-12 * max(1.0, abs(syy)):
        return float("nan")
    return 1.0 - sse / sst


def r2_score(y: F64, pred: F64) -> float:
    """Coefficient of determination; NaN when ``y`` is constant or has fewer than 2 rows."""
    return r2_combine(r2_row_stats(np.asarray(y, float), np.asarray(pred, float)).sum(axis=0))


def balanced_accuracy(y_true: npt.ArrayLike, y_pred: npt.ArrayLike) -> float:
    return float(balanced_accuracy_score(np.asarray(y_true), np.asarray(y_pred)))


def balanced_accuracy_row_stats(y_true: I64, y_pred: I64, classes: Sequence[int]) -> F64:
    """Per-row one-hot true class and one-hot correct class: balanced accuracy from sums."""
    truth = np.stack([(y_true == c) for c in classes], axis=1).astype(np.float64)
    hit = truth * (y_true == y_pred)[:, None]
    return np.concatenate([truth, hit], axis=1)


def balanced_accuracy_combine(totals: F64) -> float:
    k = len(totals) // 2
    present = totals[:k] > 0
    if not present.any():
        return float("nan")
    return float(np.mean(totals[k:][present] / totals[:k][present]))


# --- ranking statistics --------------------------------------------------------------------------


def jaccard_at_k(a: Sequence[object], b: Sequence[object], k: int = 20) -> float:
    """Jaccard index of the first ``k`` items of two ranked lists."""
    sa, sb = set(list(a)[:k]), set(list(b)[:k])
    if not sa and not sb:
        raise BootstrapError("both rankings are empty")
    return len(sa & sb) / len(sa | sb)


def random_jaccard_expectation(n: int, k: int = 20) -> float:
    """Exact expected Jaccard of two independent uniformly random k-subsets of ``n`` items.

    With overlap ``i`` hypergeometric, the Jaccard is ``i / (2k - i)``.
    """
    if n < k or k < 1:
        raise BootstrapError(f"need n >= k >= 1, got n={n}, k={k}")
    total = math.comb(n, k)
    return float(
        sum(
            math.comb(k, i) * math.comb(n - k, k - i) / total * i / (2 * k - i)
            for i in range(0, k + 1)
        )
    )


def _average_ranks(x: F64) -> F64:
    order = np.argsort(x, kind="mergesort")
    sx = x[order]
    # first and last position of each run of ties
    new = np.concatenate([[True], sx[1:] != sx[:-1]])
    starts = np.flatnonzero(new)
    ends = np.concatenate([starts[1:], [len(sx)]]) - 1
    avg = (starts + ends) / 2.0 + 1.0
    ranks = np.empty(len(x))
    ranks[order] = np.repeat(avg, ends - starts + 1)
    return ranks


def spearman(x: npt.ArrayLike, y: npt.ArrayLike) -> float:
    """Spearman rank correlation; NaN when an input is constant."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    if xa.size < 3 or np.ptp(xa) == 0 or np.ptp(ya) == 0:
        return float("nan")
    ra, rb = _average_ranks(xa), _average_ranks(ya)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    return float((ra @ rb) / np.sqrt((ra @ ra) * (rb @ rb)))
