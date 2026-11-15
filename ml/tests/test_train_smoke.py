"""Code checks on tiny mock shards (CPU, seconds). Mock losses are not results."""

from __future__ import annotations

import json
from pathlib import Path

import torch

from helpers_shards import make_mock_shards
from terraluna_ml import losses, train
from terraluna_ml.model import Encoder, TerralunaModel, grad_reverse, parameter_count


def test_shapes_and_parameter_count() -> None:
    enc = Encoder(stem_stride=1).eval()
    x = torch.zeros(2, 3, 64, 64)
    assert enc(x).shape == (2, 256)
    assert torch.allclose(
        enc.embed(torch.randn(2, 3, 64, 64)).norm(dim=1), torch.ones(2), atol=1e-6
    )
    n = parameter_count(enc)
    assert n == parameter_count(Encoder(stem_stride=2))
    assert abs(n - 2_800_000) < 0.05 * 2_800_000, n
    model = TerralunaModel().eval()
    out = model(x)
    assert (
        out["z"].shape == (2, 2048) and out["phys"].shape == (2, 11) and out["body"].shape == (2, 3)
    )
    assert enc(torch.zeros(1, 3, 64, 64)).shape == (1, 256)


def test_gradient_reversal_sign_and_scale() -> None:
    x = torch.ones(3, requires_grad=True)
    (grad_reverse(x, 0.5) * 2).sum().backward()  # type: ignore[no-untyped-call]
    assert x.grad is not None
    assert torch.allclose(x.grad, torch.full((3,), -1.0))
    y = torch.ones(3, requires_grad=True)
    (grad_reverse(y, 0.0) * 2).sum().backward()  # type: ignore[no-untyped-call]
    assert y.grad is not None and torch.all(y.grad == 0)


def test_losses_known_values() -> None:
    pred = torch.tensor([[1.0, 2.0, 3.0]])
    target = torch.tensor([[0.0, 99.0, 0.0]])
    mask = torch.tensor([[True, False, True]])
    value, count = losses.masked_physics_mse(pred, target, mask)
    assert float(value) == 5.0 and count == 2
    zero, c0 = losses.masked_physics_mse(pred, target, torch.zeros_like(mask))
    assert float(zero) == 0.0 and c0 == 0
    assert losses.dann_lambda(0.0, 0.3) == 0.0
    assert abs(losses.dann_lambda(1.0, 0.3) - 0.3) < 0.01
    collapsed = torch.ones(16, 8)
    spread = torch.randn(16, 8, generator=torch.Generator().manual_seed(0))
    assert (
        losses.vicreg_loss(collapsed, collapsed)["vicreg_var"]
        > losses.vicreg_loss(spread, spread)["vicreg_var"]
    )


def test_views_keep_fill_rule() -> None:
    x = torch.rand(4, 3, 64, 64)
    x[:, 2] = 1.0
    v = losses.make_views(x, torch.Generator().manual_seed(1))
    invalid = v[:, 2] == 0
    assert torch.all(v[:, 0][invalid] == 0) and torch.all(v[:, 1][invalid] == 0)


def test_smoke_run_and_load_backbone(tmp_path: Path) -> None:
    manifest = make_mock_shards(tmp_path / "s", per_body_split=8)
    config = train.RunConfig(
        name="smoke",
        seed=0,
        epochs=2,
        batch_size=12,
        warmup_epochs=1,
        precision="fp32",
        stem_stride=2,
    )
    out = train.run(config, manifest, tmp_path / "run", threads=1)
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["mock"] is True
    assert metrics["status"] == "completed"
    assert "PASS" not in json.dumps(metrics)
    for key in (
        "config_hash",
        "data_manifest_sha256",
        "git_sha",
        "gpu_hours",
        "deviations_from_prd",
    ):
        assert key in metrics
    enc = train.load_backbone(out)
    assert enc(torch.zeros(1, 3, 64, 64)).shape == (1, 256)
    assert train.main(["run", "--name", "x", "--seed", "0"]) == 3
    assert train.main(["run", "--name", "x", "--seed", "0", "--dry-run"]) == 0


def test_snapshot_loads_like_a_final_backbone(tmp_path: Path) -> None:
    manifest = make_mock_shards(tmp_path / "s", per_body_split=8)
    config = train.RunConfig(
        name="snap",
        seed=0,
        epochs=1,
        batch_size=12,
        warmup_epochs=1,
        precision="fp32",
        stem_stride=2,
    )
    out = train.run(config, manifest, tmp_path / "run", threads=1)
    snap = train.snapshot(out, tmp_path / "snap")
    a, b = train.load_backbone(out), train.load_backbone(snap)
    x = torch.randn(2, 3, 64, 64)
    assert torch.equal(a(x), b(x))
    assert json.loads((snap / "metrics.json").read_text())["interim_snapshot"]["epoch"] == 0
