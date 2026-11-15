"""Training objectives and views (T12A-03; PRD 11.4).

``total = L_VICReg + alpha * L_phys + lambda * L_body``. VICReg coefficients 25, 25, 1 are the
published defaults, an assumed hyperparameter (G5-05). The physics MSE averages only unmasked
entries; an all-masked batch contributes 0 and is counted, never filled. The DANN schedule is
lambda(p) = lambda_max (2 / (1 + exp(-10 p)) - 1) (Ganin et al.).

Views: one of the eight dihedral transforms, 1 m Gaussian elevation noise mapped onto the
asinh(z / 50 m) channel through its local derivative (G5-05), and 0 to 10 percent extra nodata
pixels (channels 0 and 1 set to 0.0 and the valid channel to 0: the T11-01 fill rule). No
amplitude or scale change. The 250 m sub-crop is not implemented (PRD-08).
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F
from torch import Tensor

VICREG_COEFFS = (25.0, 25.0, 1.0)
NOISE_M = 1.0
ELEVATION_SCALE_M = 50.0
MAX_HOLE_FRACTION = 0.10


def vicreg_loss(
    z1: Tensor, z2: Tensor, coeffs: tuple[float, float, float] = VICREG_COEFFS
) -> dict[str, Tensor]:
    inv = F.mse_loss(z1, z2)
    n, d = z1.shape

    def var_cov(z: Tensor) -> tuple[Tensor, Tensor]:
        z = z - z.mean(dim=0)
        std = torch.sqrt(z.var(dim=0) + 1e-4)
        var = torch.mean(F.relu(1.0 - std))
        cov = (z.T @ z) / (n - 1)
        off = cov - torch.diag(torch.diag(cov))
        return var, (off**2).sum() / d

    v1, c1 = var_cov(z1)
    v2, c2 = var_cov(z2)
    var = v1 + v2
    cov = c1 + c2
    total = coeffs[0] * inv + coeffs[1] * var + coeffs[2] * cov
    return {"vicreg": total, "vicreg_inv": inv, "vicreg_var": var, "vicreg_cov": cov}


def masked_physics_mse(pred: Tensor, target: Tensor, mask: Tensor) -> tuple[Tensor, int]:
    """Mean squared error over unmasked entries and the number of entries used."""
    m = mask.to(pred.dtype)
    count = int(mask.sum().item())
    if count == 0:
        return pred.sum() * 0.0, 0
    safe = torch.where(mask, target, torch.zeros_like(target))
    return (((pred - safe) ** 2) * m).sum() / count, count


def body_cross_entropy(logits: Tensor, body: Tensor) -> Tensor:
    return F.cross_entropy(logits, body)


def dann_lambda(progress: float, lambda_max: float) -> float:
    p = min(max(progress, 0.0), 1.0)
    return lambda_max * (2.0 / (1.0 + math.exp(-10.0 * p)) - 1.0)


def make_views(x: Tensor, generator: torch.Generator) -> Tensor:
    """One augmented view of a batch (n, 3, 64, 64); deterministic given ``generator``."""
    n = x.shape[0]
    dev = x.device
    k = int(torch.randint(0, 4, (1,), generator=generator).item())
    flip = bool(torch.randint(0, 2, (1,), generator=generator).item())
    v = torch.rot90(x, k, dims=(2, 3))
    if flip:
        v = torch.flip(v, dims=(3,))
    valid = v[:, 2:3] > 0.5
    c0 = v[:, 0:1]
    # z = 50 sinh(c0); d c0 / dz = 1 / (50 sqrt(1 + (z/50)^2)) = 1 / (50 cosh(c0)).
    noise = torch.randn(c0.shape, generator=generator).to(dev) * NOISE_M
    c0 = torch.where(valid, c0 + noise / (ELEVATION_SCALE_M * torch.cosh(c0)), c0)
    frac = torch.rand((n, 1, 1, 1), generator=generator).to(dev) * MAX_HOLE_FRACTION
    holes = torch.rand(c0.shape, generator=generator).to(dev) < frac
    keep = valid & ~holes
    zero = torch.zeros_like(c0)
    out = torch.cat(
        [torch.where(keep, c0, zero), torch.where(keep, v[:, 1:2], zero), keep.to(v.dtype)], dim=1
    )
    return out
