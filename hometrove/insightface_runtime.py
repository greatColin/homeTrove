"""Shared InsightFace FaceAnalysis singleton with refcount.

``face.image`` and ``face.video`` plugins both want the same InsightFace
model pack on disk (``buffalo_l``) and the same in-memory ``FaceAnalysis``
instance. Loading it twice wastes ~600MB of RAM, so this module owns the
singleton and exposes a refcounted acquire/release API.

* ``acquire(model_name="buffalo_l")`` — returns the shared instance, building
  it on first call. Increments the refcount.
* ``release()`` — decrements the refcount. When it hits zero, the instance
  is dropped and onnxruntime frees its model memory.
* ``ensure_downloaded(model_name)`` — drives ``FaceAnalysis.prepare()`` once
  outside of plugin startup so the user can see a clear "missing model"
  error during the manual download flow instead of during boot.

InsightFace is heavy (~50ms cold import) and the underlying ``FaceAnalysis``
object pins several onnxruntime sessions, so the lifecycle is intentional
rather than opportunistic.

This module is intentionally dependency-light: importing it must not pull in
``insightface`` at module load time, only when ``acquire()`` is called.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional


# InsightFace packs we know about. dim matches ``face_embedding_models.dim``.
_KNOWN_MODELS: dict[str, int] = {
    "buffalo_l": 512,
    "buffalo_s": 512,
    "buffalo_m": 512,
    "antelopev2": 512,
}


class ModelMissingError(RuntimeError):
    """Raised when the on-disk InsightFace pack is not present.

    The download worker catches this and offers the user a button to trigger
    ``FaceAnalysis.prepare()`` which auto-downloads the model from the
    InsightFace CDN.
    """

    def __init__(self, model_name: str, model_dir: str):
        super().__init__(
            f"InsightFace model {model_name!r} not found at {model_dir!s}. "
            "请通过「下载模型并重启」按钮触发自动下载。"
        )
        self.model_name = model_name
        self.model_dir = model_dir


class _RuntimeState:
    def __init__(self) -> None:
        self.instance: Optional[Any] = None
        self.refcount: int = 0
        self.model_name: Optional[str] = None
        self.lock = threading.Lock()


_STATE = _RuntimeState()


def _model_dir(model_name: str) -> Path:
    """Where InsightFace looks for the unpacked model files.

    Always returns the *leaf* path ``~/.insightface/models/<model_name>``,
    whether or not the files exist yet. That lets the caller distinguish
    "pack present" (skip the downloader) from "pack missing" (let
    ``FaceAnalysis`` try to download it) purely via ``Path.exists()``.
    """
    from pathlib import Path as _P

    try:
        from insightface.utils import get_model_dir  # type: ignore

        # ``get_model_dir(name)`` is the canonical leaf path helper on
        # InsightFace 0.7+; use it when it actually points at existing
        # files.
        candidate = _P(get_model_dir(model_name))
        if candidate.exists():
            return candidate
    except Exception:
        pass
    # Fallback for versions where ``get_model_dir`` is missing or the pack
    # has not been downloaded yet.
    return _P("~/.insightface").expanduser() / "models" / model_name


def _build(model_name: str) -> Any:
    """Build (and download if missing) a FaceAnalysis instance."""
    from insightface.app import FaceAnalysis  # type: ignore

    model_path = _model_dir(model_name)
    if not model_path.exists():
        # Model pack missing — let InsightFace try its own downloader.
        # If that fails we re-raise as ModelMissingError for a friendly UI
        # message instead of a raw stack trace.
        try:
            app = FaceAnalysis(
                name=model_name,
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=0, det_size=(640, 640))
        except Exception as exc:
            raise ModelMissingError(model_name, str(model_path)) from exc
        return app
    app = FaceAnalysis(
        name=model_name,
        # InsightFace 0.7+ resolves ``<root>/models/<name>`` internally —
        # we want that final segment to land at ``~/.insightface/models/
        # <name>`` (the canonical path), so we pass
        # ``~/.insightface`` (the *grandparent* of the existing model dir)
        # as the root. Otherwise the downloader nests into
        # ``<root>/models/models/<name>``.
        root=str(model_path.parent.parent),
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    try:
        app.prepare(ctx_id=0, det_size=(640, 640))
    except Exception as exc:
        # Loading the onnx pack can fail for many reasons (corrupt download,
        # onnxruntime protobuf mismatch, missing CPU provider). Wrap any of
        # those into ModelMissingError so the UI can prompt the user to
        # re-download instead of showing a raw stack trace.
        raise ModelMissingError(model_name, str(model_path)) from exc
    return app


def acquire(model_name: str = "buffalo_l") -> Any:
    """Return the shared FaceAnalysis instance, building it on first call.

    Thread-safe. Each plugin must pair this with exactly one ``release()``
    call (typically in ``shutdown()`` with a try/finally guard).
    """
    if model_name not in _KNOWN_MODELS:
        raise ValueError(f"unknown InsightFace model: {model_name!r}")
    with _STATE.lock:
        if _STATE.instance is None:
            _STATE.instance = _build(model_name)
            _STATE.model_name = model_name
        elif _STATE.model_name != model_name:
            raise RuntimeError(
                f"InsightFaceRuntime already loaded {_STATE.model_name!r}; "
                f"cannot swap to {model_name!r} without an explicit reset"
            )
        _STATE.refcount += 1
        return _STATE.instance


def release() -> None:
    """Drop one refcount; free the instance when it reaches zero.

    Safe to call more times than ``acquire()`` — the count is clamped at 0
    so a stray release in a finally block can never go negative.
    """
    with _STATE.lock:
        if _STATE.refcount <= 0:
            return
        _STATE.refcount -= 1
        if _STATE.refcount == 0:
            _STATE.instance = None
            _STATE.model_name = None


def refcount() -> int:
    """Current refcount (mainly for tests / status display)."""
    with _STATE.lock:
        return _STATE.refcount


def is_loaded() -> bool:
    with _STATE.lock:
        return _STATE.instance is not None


def model_dim(model_name: str = "buffalo_l") -> int:
    """Return the vector dimension for a known InsightFace model."""
    if model_name not in _KNOWN_MODELS:
        raise ValueError(f"unknown InsightFace model: {model_name!r}")
    return _KNOWN_MODELS[model_name]


def ensure_downloaded(model_name: str = "buffalo_l") -> bool:
    """Drive ``FaceAnalysis.prepare()`` once to trigger InsightFace's own
    downloader. Returns True when the model files are present afterwards.

    Used by the model-download worker: the call is wrapped in try/except
    so the API can surface a 4xx/5xx with a clear message instead of a raw
    onnxruntime stack trace.
    """
    if model_name not in _KNOWN_MODELS:
        raise ValueError(f"unknown InsightFace model: {model_name!r}")
    from insightface.app import FaceAnalysis  # type: ignore

    try:
        # A throwaway instance — we just want prepare() to materialize
        # the model on disk. We then release it without touching the
        # singleton refcount.
        app = FaceAnalysis(
            name=model_name,
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=0, det_size=(640, 640))
        del app
        return _model_dir(model_name).exists()
    except Exception:
        return False


def reset_for_test() -> None:
    """Test helper: wipe singleton state without going through release()."""
    with _STATE.lock:
        _STATE.instance = None
        _STATE.refcount = 0
        _STATE.model_name = None