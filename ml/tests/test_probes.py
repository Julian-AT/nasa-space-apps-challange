from __future__ import annotations

import numpy as np

from terraluna_ml import probes
from terraluna_ml.probes import EmbeddingSet, Unavailable


def _set(rng: np.random.Generator, n: int, d: int = 8) -> tuple[EmbeddingSet, np.ndarray]:
    body = rng.integers(0, 3, size=n).astype(np.int64)
    x = rng.normal(size=(n, d)).astype(np.float32)
    x[:, 0] += 3.0 * body  # body is linearly decodable
    block = np.array([f"b{b}-{i % 6}" for i, b in enumerate(body)])
    ids = np.array([f"w{i}" for i in range(n)])
    return EmbeddingSet(x, np.arange(n, dtype=np.int64), body, block, ids), body


def _targets(es: EmbeddingSet, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    y = np.column_stack([es.x[:, 1] * 2.0 + es.x[:, 2], rng.normal(size=len(es))]).astype(float)
    mask = np.ones(y.shape, dtype=bool)
    mask[::7, 0] = False
    y[~mask] = np.nan
    return y, mask


def test_physics_probe_recovers_signal_and_not_noise() -> None:
    rng = np.random.default_rng(0)
    tr, _ = _set(rng, 400)
    va, _ = _set(rng, 100)
    te, _ = _set(rng, 200)
    (ytr, mtr), (yva, mva), (yte, mte) = (_targets(s, rng) for s in (tr, va, te))
    probe = probes.fit_physics_probe(tr, ytr, mtr, va, yva, mva, te, yte, mte, ("signal", "noise"))
    sig = probes.physics_r2(probe, "signal", n_resamples=100)
    noise = probes.physics_r2(probe, "noise", n_resamples=100)
    assert not isinstance(sig, Unavailable) and not isinstance(noise, Unavailable)
    assert sig.estimate > 0.95 and sig.ci_low > 0.9
    assert noise.estimate < 0.2
    # masked entries are excluded, never zero-filled
    assert np.isnan(probe.pred[:, 0]).sum() == 0 and probe.mask[:, 0].sum() < len(te)
    per_body = probes.physics_r2(probe, "signal", body=1, n_resamples=50)
    assert not isinstance(per_body, Unavailable)
    diff = probes.paired_physics_r2_diff(probe, probe, "signal", n_resamples=50)
    assert not isinstance(diff, Unavailable) and diff.estimate == 0.0


def test_physics_probe_unavailable_when_no_valid_rows() -> None:
    rng = np.random.default_rng(1)
    tr, _ = _set(rng, 60)
    va, _ = _set(rng, 30)
    te, _ = _set(rng, 30)
    y = np.full((60, 1), np.nan)
    m = np.zeros((60, 1), dtype=bool)
    probe = probes.fit_physics_probe(tr, y, m, va, y[:30], m[:30], te, y[:30], m[:30], ("t",))
    out = probes.physics_r2(probe, "t")
    assert isinstance(out, Unavailable)


def test_body_probe_above_chance_and_chance_from_classes() -> None:
    rng = np.random.default_rng(2)
    tr, _ = _set(rng, 300)
    va, _ = _set(rng, 90)
    te, _ = _set(rng, 150)
    probe = probes.fit_body_probe(tr, va, te)
    ba = probes.body_balanced_accuracy(probe, n_resamples=100)
    assert not isinstance(ba, Unavailable)
    assert probe.chance == 1 / 3
    assert ba.estimate > 0.8 and ba.ci_low <= ba.estimate <= ba.ci_high + 1e-12


def test_body_probe_noise_is_near_chance() -> None:
    rng = np.random.default_rng(3)

    def noisy(n: int) -> EmbeddingSet:
        es, _ = _set(rng, n)
        return EmbeddingSet(
            rng.normal(size=es.x.shape).astype(np.float32),
            es.rows,
            es.body_index,
            es.eval_block,
            es.window_id,
        )

    probe = probes.fit_body_probe(noisy(300), noisy(90), noisy(300))
    ba = probes.body_balanced_accuracy(probe, n_resamples=50)
    assert not isinstance(ba, Unavailable) and ba.estimate < 0.5


def test_morphology_is_unavailable_with_reason() -> None:
    out = probes.morphology_probe()
    assert isinstance(out, Unavailable)
    assert "Robbins" in out.reason
