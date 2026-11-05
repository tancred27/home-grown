"""Is the GPU actually being used? Run with .venv-gpu.

onnxruntime falls back to CPU silently when a CUDA or cuDNN DLL is missing, so a job
that "ran fine" proves nothing. This asks the live session which provider it is on, and
times the same encoding on CPU and on GPU.

Usage: .venv-gpu/Scripts/python -I scripts/check_gpu.py [model] [n_texts]
"""

from __future__ import annotations

import sys
import time

import onnxruntime as ort
from fastembed import TextEmbedding

from orgmem.device import _cuda_ready, active_providers


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-large-en-v1.5"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 256

    print(f"onnxruntime {ort.__version__}")
    print(f"available providers: {ort.get_available_providers()}")
    print(f"CUDA usable after preload_dlls: {_cuda_ready()}\n")

    # Realistic length: asset 'card' texts run to a few hundred tokens.
    texts = [("Asserted by its own tests: rejects a payload larger than the configured "
              "maximum and returns a clear error to the caller. ") * 6] * n

    results = {}
    for label, prov in (("cpu", ["CPUExecutionProvider"]),
                        ("cuda", ["CUDAExecutionProvider", "CPUExecutionProvider"])):
        if label == "cuda" and not _cuda_ready():
            print("cuda: skipped, CUDAExecutionProvider not usable")
            continue
        m = TextEmbedding(model_name=model, providers=prov)
        actual = active_providers(m)
        list(m.embed(texts[:16], batch_size=16))            # warm up / kernel init
        t = time.perf_counter()
        list(m.embed(texts, batch_size=32))
        dt = time.perf_counter() - t
        results[label] = dt
        print(f"{label:5s} requested={prov[0]:24s} actual={actual[0]:24s} "
              f"{n} texts in {dt:6.2f}s  ({n/dt:6.1f}/s)")
        if label == "cuda" and actual[0] != "CUDAExecutionProvider":
            print("  !! requested CUDA but the session is on CPU -- silent fallback")

    if len(results) == 2:
        print(f"\nspeedup: {results['cpu'] / results['cuda']:.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
