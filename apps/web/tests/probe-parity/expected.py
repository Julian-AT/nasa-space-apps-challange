"""Python onnxruntime (CPU) embeddings of the parity windows, for the WASM parity test (T13-03).

Run with the ml environment: ``uv run --locked python -I apps/web/tests/probe-parity/expected.py
<model dir> ml/checkpoints/onnx-parity``. Reads windows.f32.bin, runs encoder.onnx (fp32) and
encoder.fp16.onnx, and writes expected_fp32.f32.bin, expected_fp16.f32.bin and expected.json.
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort


def main() -> int:
    model_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    meta = json.loads((out / "windows.json").read_text())
    x = np.fromfile(out / "windows.f32.bin", dtype="<f4").reshape(meta["shape"])
    record: dict[str, object] = {"runtime": f"onnxruntime {ort.__version__} CPUExecutionProvider", "count": int(x.shape[0])}
    for name, tag in (("encoder.onnx", "fp32"), ("encoder.fp16.onnx", "fp16")):
        path = model_dir / name
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        y = np.concatenate([session.run(["embedding"], {"windows": x[i : i + 32]})[0] for i in range(0, len(x), 32)])
        y.astype("<f4").tofile(out / f"expected_{tag}.f32.bin")
        record[tag] = {"model": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (out / "expected.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"expected.py: wrote {x.shape[0]} embeddings per model to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
