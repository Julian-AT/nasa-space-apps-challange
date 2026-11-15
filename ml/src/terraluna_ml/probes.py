"""Frozen-embedding probes (PRD 11.6): physics ridge, body logistic, morphology (Unavailable).

Embeddings are frozen; preprocessing (standardisation) is fitted on the training rows only. The
regularisation strength is chosen on the validation rows from a small fixed grid and recorded.
Predictions are fixed before the bootstrap, so the intervals are over spatial blocks of the
evaluated rows only. Masked physics entries (Unavailable, never zero) are excluded per target.
Every statistic here is Predicted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

from terraluna_ml import stats

F32 = npt.NDArray[np.float32]
F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]

RIDGE_ALPHAS: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
LOGISTIC_CS: tuple[float, ...] = (0.001, 0.01, 0.1, 1.0, 10.0)
MIN_ROWS = 3

MORPHOLOGY_UNAVAILABLE_REASON = (
    "crater labels do not exist in this dataset: no Robbins catalogue is ingested, so no log "
    "crater density can be formed for Moon or Mars windows"
)


@dataclass(frozen=True)
class Unavailable:
    """A test that cannot be computed. Carries its reason; never a value."""

    reason: str


@dataclass(frozen=True)
class EmbeddingSet:
    """Frozen embeddings of a set of shard rows with the columns the probes need."""

    x: F32
    rows: I64
    body_index: I64
    eval_block: npt.NDArray[np.str_]
    window_id: npt.NDArray[np.str_]

    def __post_init__(self) -> None:
        n = self.x.shape[0]
        for name in ("rows", "body_index", "eval_block", "window_id"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"EmbeddingSet column {name} does not match {n} embeddings")

    def __len__(self) -> int:
        return int(self.x.shape[0])

    def take(self, index: npt.NDArray[np.int64] | npt.NDArray[np.bool_]) -> EmbeddingSet:
        return EmbeddingSet(
            self.x[index],
            self.rows[index],
            self.body_index[index],
            self.eval_block[index],
            self.window_id[index],
        )


@dataclass
class PhysicsProbe:
    """Ridge predictions on the evaluated rows, one column per physics target."""

    targets: tuple[str, ...]
    alphas: list[float | None]
    alpha_note: list[str]
    pred: F64
    y: F64
    mask: npt.NDArray[np.bool_]
    body_index: I64
    eval_block: npt.NDArray[np.str_]
    unavailable: dict[str, str] = field(default_factory=dict)


def _scaler(train_x: F32) -> StandardScaler:
    return StandardScaler().fit(train_x)


def fit_physics_probe(
    train: EmbeddingSet,
    y_train: F64,
    mask_train: npt.NDArray[np.bool_],
    val: EmbeddingSet,
    y_val: F64,
    mask_val: npt.NDArray[np.bool_],
    test: EmbeddingSet,
    y_test: F64,
    mask_test: npt.NDArray[np.bool_],
    targets: tuple[str, ...],
    alphas: tuple[float, ...] = RIDGE_ALPHAS,
) -> PhysicsProbe:
    """Ridge per target on train embeddings; alpha per target chosen on validation R2."""
    sc = _scaler(train.x)
    xtr, xva, xte = (sc.transform(s.x) for s in (train, val, test))
    pred = np.full(y_test.shape, np.nan)
    chosen: list[float | None] = []
    notes: list[str] = []
    unavailable: dict[str, str] = {}
    for j, name in enumerate(targets):
        tr = mask_train[:, j]
        if int(tr.sum()) < MIN_ROWS:
            chosen.append(None)
            notes.append("fewer than 3 valid training rows")
            unavailable[name] = f"fewer than {MIN_ROWS} valid training rows for {name}"
            continue
        va = mask_val[:, j]
        best_alpha, note = alphas[len(alphas) // 2], "no usable validation rows; middle alpha"
        if int(va.sum()) >= MIN_ROWS and np.ptp(y_val[va, j]) > 0:
            scores = [
                stats.r2_score(
                    y_val[va, j],
                    Ridge(alpha=a).fit(xtr[tr], y_train[tr, j]).predict(xva[va]),
                )
                for a in alphas
            ]
            best_alpha = alphas[int(np.nanargmax(scores))]
            note = "alpha chosen on validation R2"
        model = Ridge(alpha=best_alpha).fit(xtr[tr], y_train[tr, j])
        pred[:, j] = model.predict(xte)
        chosen.append(best_alpha)
        notes.append(note)
    return PhysicsProbe(
        targets=targets,
        alphas=chosen,
        alpha_note=notes,
        pred=pred,
        y=y_test,
        mask=mask_test,
        body_index=test.body_index,
        eval_block=test.eval_block,
        unavailable=unavailable,
    )


def physics_r2(
    probe: PhysicsProbe,
    target: str,
    *,
    body: int | None = None,
    n_resamples: int = stats.N_RESAMPLES,
    seed: int = 0,
) -> stats.Interval | Unavailable:
    """R2 of one target on test rows (pooled over bodies, or one body) with a block interval."""
    j = probe.targets.index(target)
    if target in probe.unavailable:
        return Unavailable(probe.unavailable[target])
    use = probe.mask[:, j] & np.isfinite(probe.pred[:, j])
    if body is not None:
        use &= probe.body_index == body
    n = int(use.sum())
    if n < MIN_ROWS:
        return Unavailable(f"only {n} valid test rows for {target}")
    blocks = probe.eval_block[use]
    if len(np.unique(blocks)) < 2:
        return Unavailable(f"fewer than 2 spatial test blocks for {target}")
    y, p = probe.y[use, j], probe.pred[use, j]
    if not np.ptp(y) > 0:
        return Unavailable(f"the test targets for {target} are constant")
    return stats.block_bootstrap_sums(
        blocks,
        stats.r2_row_stats(y, p),
        stats.r2_combine,
        n_resamples=n_resamples,
        seed=seed,
    )


def paired_physics_r2_diff(
    a: PhysicsProbe,
    b: PhysicsProbe,
    target: str,
    *,
    body: int | None = None,
    n_resamples: int = stats.N_RESAMPLES,
    seed: int = 0,
) -> stats.Interval | Unavailable:
    """Paired block bootstrap of ``R2(a) - R2(b)`` on the same test rows."""
    j = a.targets.index(target)
    if target in a.unavailable or target in b.unavailable:
        return Unavailable(f"a probe has no fit for {target}")
    use = a.mask[:, j] & np.isfinite(a.pred[:, j]) & np.isfinite(b.pred[:, j])
    if body is not None:
        use &= a.body_index == body
    if int(use.sum()) < MIN_ROWS or len(np.unique(a.eval_block[use])) < 2:
        return Unavailable(f"too few valid test rows or blocks for the paired {target} interval")
    y = a.y[use, j]
    return stats.paired_block_bootstrap_sums(
        a.eval_block[use],
        stats.r2_row_stats(y, a.pred[use, j]),
        stats.r2_combine,
        stats.r2_row_stats(y, b.pred[use, j]),
        stats.r2_combine,
        n_resamples=n_resamples,
        seed=seed,
    )


@dataclass
class BodyProbe:
    c: float
    classes: tuple[int, ...]
    chance: float
    predicted: I64
    truth: I64
    eval_block: npt.NDArray[np.str_]
    note: str


def fit_body_probe(
    train: EmbeddingSet,
    val: EmbeddingSet,
    test: EmbeddingSet,
    cs: tuple[float, ...] = LOGISTIC_CS,
) -> BodyProbe:
    """Multinomial logistic regression for the body; C chosen on validation balanced accuracy."""
    classes = tuple(int(c) for c in np.unique(train.body_index))
    if len(classes) < 2:
        raise ValueError("the body probe needs at least two bodies in the training rows")
    sc = _scaler(train.x)
    xtr, xva, xte = (sc.transform(s.x) for s in (train, val, test))
    best_c, note = cs[len(cs) // 2], "no usable validation rows; middle C"
    if len(val) > 0 and len(np.unique(val.body_index)) >= 2:
        scores = [
            stats.balanced_accuracy(
                val.body_index,
                LogisticRegression(C=c, max_iter=1000).fit(xtr, train.body_index).predict(xva),
            )
            for c in cs
        ]
        best_c = cs[int(np.argmax(scores))]
        note = "C chosen on validation balanced accuracy"
    model = LogisticRegression(C=best_c, max_iter=1000).fit(xtr, train.body_index)
    return BodyProbe(
        c=best_c,
        classes=classes,
        chance=1.0 / len(classes),
        predicted=model.predict(xte).astype(np.int64),
        truth=test.body_index.astype(np.int64),
        eval_block=test.eval_block,
        note=note,
    )


def body_balanced_accuracy(
    probe: BodyProbe, *, n_resamples: int = stats.N_RESAMPLES, seed: int = 0
) -> stats.Interval | Unavailable:
    if len(np.unique(probe.eval_block)) < 2:
        return Unavailable("fewer than 2 spatial test blocks")
    return stats.block_bootstrap_sums(
        probe.eval_block,
        stats.balanced_accuracy_row_stats(probe.truth, probe.predicted, probe.classes),
        stats.balanced_accuracy_combine,
        n_resamples=n_resamples,
        seed=seed,
    )


def morphology_probe() -> Unavailable:
    """The crater-density probe of PRD 11.6; there are no crater labels in this dataset."""
    return Unavailable(MORPHOLOGY_UNAVAILABLE_REASON)
