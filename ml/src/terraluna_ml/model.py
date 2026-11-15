"""Terraluna encoder (T12A-02; PRD 11.3). EXPERIMENTAL until G5; outputs are Predicted.

Backbone: ResNet-18 layout (BasicBlock, [2, 2, 2, 2]) at half width (32, 64, 128, 256), a 3 x 3
stem without max-pooling and global average pooling to a 256-value embedding
(``ml.embedding_dim``). PRD 11.3 specifies a stride-1 stem; ``stem_stride`` keeps that default
and allows 2, which quarters the compute. The reduced-scope CPU run (team rule: no GPU) records
the stem stride it used as a deviation; the parameter count does not depend on it.

Heads: a VICReg projector (3-layer MLP, 2048 wide), a physics head (2-layer MLP, 11 outputs)
and a body head (2-layer MLP, 3 logits) behind a gradient-reversal function whose lambda is set
at run time. Only the backbone is exported.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F
from torch import Tensor, nn

EMBEDDING_DIM = 256
PHYS_DIM = 11
N_BODIES = 3
WIDTHS = (32, 64, 128, 256)
PROJECTOR_DIM = 2048


class _GradReverse(torch.autograd.Function):
    @staticmethod
    def forward(ctx: Any, x: Tensor, lam: float) -> Tensor:
        ctx.lam = lam
        return x.view_as(x)

    @staticmethod
    def backward(ctx: Any, grad: Tensor) -> tuple[Tensor, None]:
        return -ctx.lam * grad, None


def grad_reverse(x: Tensor, lam: float) -> Tensor:
    """Identity forward; the backward pass multiplies the gradient by ``-lam``."""
    out: Tensor = _GradReverse.apply(x, lam)
    return out


class BasicBlock(nn.Module):
    def __init__(self, cin: int, cout: int, stride: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(cin, cout, 3, stride, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(cout)
        self.conv2 = nn.Conv2d(cout, cout, 3, 1, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(cout)
        self.down: nn.Module = nn.Identity()
        if stride != 1 or cin != cout:
            self.down = nn.Sequential(
                nn.Conv2d(cin, cout, 1, stride, bias=False), nn.BatchNorm2d(cout)
            )

    def forward(self, x: Tensor) -> Tensor:
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + self.down(x))


class Encoder(nn.Module):
    """The backbone: (n, 3, 64, 64) -> pooled (n, 256)."""

    def __init__(self, stem_stride: int = 1) -> None:
        super().__init__()
        if stem_stride not in (1, 2):
            raise ValueError("stem_stride must be 1 (PRD 11.3) or 2 (recorded deviation)")
        self.stem_stride = stem_stride
        self.stem = nn.Sequential(
            nn.Conv2d(3, WIDTHS[0], 3, stem_stride, 1, bias=False),
            nn.BatchNorm2d(WIDTHS[0]),
            nn.ReLU(inplace=True),
        )
        layers: list[nn.Module] = []
        cin = WIDTHS[0]
        for i, w in enumerate(WIDTHS):
            stride = 1 if i == 0 else 2
            layers.append(nn.Sequential(BasicBlock(cin, w, stride), BasicBlock(w, w, 1)))
            cin = w
        self.layers = nn.Sequential(*layers)

    def forward(self, x: Tensor) -> Tensor:
        h = self.layers(self.stem(x))
        out: Tensor = torch.flatten(F.adaptive_avg_pool2d(h, 1), 1)
        return out

    def embed(self, x: Tensor) -> Tensor:
        """The L2-normalised embedding used for retrieval."""
        return F.normalize(self.forward(x), dim=1)


def _mlp(dims: tuple[int, ...]) -> nn.Sequential:
    mods: list[nn.Module] = []
    for i in range(len(dims) - 1):
        mods.append(nn.Linear(dims[i], dims[i + 1]))
        if i < len(dims) - 2:
            mods.extend([nn.BatchNorm1d(dims[i + 1]), nn.ReLU(inplace=True)])
    return nn.Sequential(*mods)


class TerralunaModel(nn.Module):
    def __init__(self, *, stem_stride: int = 1, use_phys_head: bool = True) -> None:
        super().__init__()
        self.backbone = Encoder(stem_stride)
        self.projector = _mlp((EMBEDDING_DIM, PROJECTOR_DIM, PROJECTOR_DIM, PROJECTOR_DIM))
        self.phys_head: nn.Module | None = (
            _mlp((EMBEDDING_DIM, EMBEDDING_DIM, PHYS_DIM)) if use_phys_head else None
        )
        self.body_head = _mlp((EMBEDDING_DIM, EMBEDDING_DIM, N_BODIES))
        self.grl_lambda = 0.0

    def forward(self, x: Tensor) -> dict[str, Tensor]:
        h = self.backbone(x)
        out = {
            "h": h,
            "z": self.projector(h),
            "body": self.body_head(grad_reverse(h, self.grl_lambda)),
        }
        if self.phys_head is not None:
            out["phys"] = self.phys_head(h)
        return out

    def backbone_state_dict(self) -> dict[str, Tensor]:
        return dict(self.backbone.state_dict())


def parameter_count(module: nn.Module) -> int:
    return sum(p.numel() for p in module.parameters())
