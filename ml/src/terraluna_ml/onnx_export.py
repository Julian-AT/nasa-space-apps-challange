"""ONNX export of a trained encoder backbone (PRD 8.8, 11.8). EXPERIMENTAL; outputs Predicted.

::

    python -m terraluna_ml.onnx_export --run <run dir> --manifest <shards>/manifest.json --out DIR

Writes ``encoder.onnx`` (fp32) and ``encoder.fp16.onnx`` (fp16 weights, float32 inputs and
outputs kept), opset 17 with a dynamic batch axis. The graph takes ``windows`` float32
``(n, 3, 64, 64)`` (the channels of ``dataset.CHANNELS``; preprocessing stays outside the graph)
and returns ``embedding`` float32 ``(n, 256)``, L2-normalised inside the graph
(``l2_normalized: true``). Parity is measured, not asserted: on up to 256 validation windows of
the shards (never test blocks), the Python onnxruntime fp32 max-abs difference to torch and the
fp16 minimum cosine to torch are recorded in ``export_record.json`` beside the thresholds of
PRD 8.8 (1e-4 and 0.999) and whether they hold. The WASM parity run belongs to the web probe.

Also writes ``background_<target_id>.json`` per target row (ModelBackground, CONTRACTS a.4):
the target's embedding and the histogram of its cosine distances to the Earth validation
windows (smaller is closer), for the probe's percentile guard.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import Tensor, nn

from terraluna_ml.shards import canonical_json, open_shards, sha256_path
from terraluna_ml.train import load_backbone

OPSET = 17
PARITY_FP32_MAX_ABS = 1e-4
PARITY_FP16_MIN_COS = 0.999


class _Embed(nn.Module):
    def __init__(self, backbone: nn.Module) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, windows: Tensor) -> Tensor:
        h = self.backbone(windows)
        out: Tensor = h / torch.clamp(torch.linalg.vector_norm(h, dim=1, keepdim=True), min=1e-12)
        return out


def _embed_torch(module: nn.Module, x: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    with torch.no_grad():
        out: np.ndarray[Any, Any] = module(torch.from_numpy(x)).numpy()
    return out


def main(argv: Sequence[str] | None = None) -> int:
    import onnx
    import onnxruntime as ort
    from onnxruntime.transformers.float16 import convert_float_to_float16

    parser = argparse.ArgumentParser(prog="terraluna_ml.onnx_export")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    args = parser.parse_args(argv)
    shards = open_shards(args.manifest)
    if shards.mock:
        print("refusing to export from mock shards", file=sys.stderr)
        return 1
    backbone = load_backbone(args.run)
    module = _Embed(backbone).eval()
    args.out.mkdir(parents=True, exist_ok=True)
    fp32_path = args.out / "encoder.onnx"
    fp16_path = args.out / "encoder.fp16.onnx"
    dummy = torch.zeros(2, 3, 64, 64)
    torch.onnx.export(
        module,
        (dummy,),
        str(fp32_path),
        input_names=["windows"],
        output_names=["embedding"],
        dynamic_axes={"windows": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=OPSET,
        dynamo=False,
    )
    model32 = onnx.load(str(fp32_path))
    onnx.checker.check_model(model32)
    model16 = convert_float_to_float16(model32, keep_io_types=True)
    onnx.save(model16, str(fp16_path))

    split = shards.column("split")
    val_rows = np.flatnonzero(split == "val")
    rng = np.random.default_rng(0)
    rows = np.sort(rng.choice(val_rows, size=min(256, val_rows.size), replace=False))
    x = np.asarray(shards.windows[rows], dtype=np.float32)
    ref = _embed_torch(module, x)
    s32 = ort.InferenceSession(str(fp32_path), providers=["CPUExecutionProvider"])
    s16 = ort.InferenceSession(str(fp16_path), providers=["CPUExecutionProvider"])
    o32 = s32.run(["embedding"], {"windows": x})[0]
    o16 = s16.run(["embedding"], {"windows": x})[0].astype(np.float64)
    max_abs = float(np.max(np.abs(o32 - ref)))
    cos = np.sum(o16 * ref, axis=1) / (np.linalg.norm(o16, axis=1) * np.linalg.norm(ref, axis=1))
    min_cos = float(np.min(cos))

    run_metrics = json.loads((args.run / "metrics.json").read_text())
    record = {
        "model_id": args.model_id,
        "status": "EXPERIMENTAL",
        "evidence_label": "Predicted",
        "mock": False,
        "model_source": "trained",
        "source_run": args.run.name,
        "source_checkpoint_sha256": sha256_path(args.run / "backbone.pt"),
        "config_hash": run_metrics["config_hash"],
        "data_manifest_sha256": shards.data_manifest_sha256,
        "opset": OPSET,
        "input_name": "windows",
        "input_shape": ["batch", 3, 64, 64],
        "input_dtype": "float32",
        "output_name": "embedding",
        "output_shape": ["batch", 256],
        "l2_normalized": True,
        "preprocessing": "outside the graph: dataset.CHANNELS (asinh(z/50 m), slope/45 deg, valid)",
        "files": {
            "encoder.onnx": {"sha256": sha256_path(fp32_path), "bytes": fp32_path.stat().st_size},
            "encoder.fp16.onnx": {
                "sha256": sha256_path(fp16_path),
                "bytes": fp16_path.stat().st_size,
                "conversion": "onnxruntime.transformers.float16, keep_io_types=True",
            },
        },
        "python_parity": {
            "n_windows": int(rows.size),
            "windows_from": "validation split of the shards (never test blocks)",
            "fp32_max_abs_diff": max_abs,
            "fp32_threshold": PARITY_FP32_MAX_ABS,
            "fp32_within": max_abs <= PARITY_FP32_MAX_ABS,
            "fp16_min_cosine": min_cos,
            "fp16_threshold": PARITY_FP16_MIN_COS,
            "fp16_within": min_cos >= PARITY_FP16_MIN_COS,
            "runtime": f"onnxruntime {ort.__version__} CPUExecutionProvider",
        },
        "wasm_parity": {
            "status": "Unavailable",
            "reason": "run by the web probe (Vitest onnxruntime-web)",
        },
    }
    (args.out / "export_record.json").write_text(canonical_json(record), encoding="utf-8")

    # Backgrounds per target: cosine distance of the target to Earth validation windows.
    targets = np.flatnonzero(split == "target")
    earth_val = np.flatnonzero((split == "val") & (shards.column("body") == "earth"))
    ids = shards.column("window_id")
    emb_bg = np.concatenate(
        [
            s32.run(
                ["embedding"],
                {"windows": np.asarray(shards.windows[earth_val[i : i + 256]], dtype=np.float32)},
            )[0]
            for i in range(0, earth_val.size, 256)
        ]
    )
    for t in targets:
        te = s32.run(
            ["embedding"], {"windows": np.asarray(shards.windows[t : t + 1], dtype=np.float32)}
        )[0][0]
        d = 1.0 - emb_bg @ te
        counts, edges = np.histogram(d, bins=50, range=(0.0, max(float(np.max(d)), 1e-6)))
        bg = {
            "target_id": str(ids[t]),
            "family": "model_embedding",
            "model_id": args.model_id,
            "onnx_sha256": record["files"]["encoder.onnx"]["sha256"],
            "bin_edges": [float(e) for e in edges],
            "counts": [int(c) for c in counts],
            "percentile_definition": "linear_within_bin_v1",
            "direction": "smaller_is_closer",
            "target_embedding": [float(v) for v in te],
            "background_windows": f"{earth_val.size} Earth validation windows of the shards",
            "evidence_label": "Predicted",
        }
        (args.out / f"background_{ids[t]}.json").write_text(canonical_json(bg), encoding="utf-8")
    print(canonical_json(record["python_parity"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
