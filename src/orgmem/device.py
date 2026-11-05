"""Which onnxruntime execution provider to run models on.

Two environments exist on purpose:
  .venv      onnxruntime (CPU build)
  .venv-gpu  onnxruntime-gpu + CUDA 12 / cuDNN 9 wheels
They cannot share one environment: both install the same `onnxruntime` module.

The same code runs in both. With `device="auto"` (the default) CUDA is used when the
installed onnxruntime can see it, and CPU otherwise.

On Windows the CUDA/cuDNN DLLs from the nvidia-* pip wheels are not on PATH, so
onnxruntime cannot find them on its own; `preload_dlls()` loads them explicitly. Without
that call onnxruntime does not raise -- it quietly falls back to CPU, which is why
`active_providers()` exists: always confirm the provider rather than trusting the request.
"""

from __future__ import annotations

import functools

from .config import settings


@functools.lru_cache(maxsize=1)
def _cuda_ready() -> bool:
    try:
        import onnxruntime as ort
    except ImportError:
        return False
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        return False
    preload = getattr(ort, "preload_dlls", None)
    if preload is not None:
        try:
            preload()
        except Exception:      # missing wheels -> CUDA would fail at session creation
            return False
    return True


def providers() -> list[str]:
    """Execution providers for a new model session, best first."""
    want = settings.device.lower()
    if want == "cpu":
        return ["CPUExecutionProvider"]
    if want in ("cuda", "gpu"):
        if not _cuda_ready():
            raise RuntimeError(
                "device=cuda but CUDAExecutionProvider is unavailable. Use .venv-gpu, "
                "or set ORGMEM_DEVICE=cpu.")
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return (["CUDAExecutionProvider", "CPUExecutionProvider"] if _cuda_ready()
            else ["CPUExecutionProvider"])


def active_providers(model) -> list[str]:
    """What a fastembed model's onnx session is ACTUALLY using.

    Requesting CUDA does not guarantee it: onnxruntime falls back to CPU silently when a
    DLL is missing. Walk the wrapper to the InferenceSession and ask it.
    """
    obj = model
    for attr in ("model", "model", "model"):
        if hasattr(obj, "get_providers"):
            break
        obj = getattr(obj, attr, None)
        if obj is None:
            return ["unknown"]
    try:
        return list(obj.get_providers())
    except Exception:
        return ["unknown"]
