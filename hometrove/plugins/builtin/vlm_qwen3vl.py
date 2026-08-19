"""``vlm.qwen3vl`` — Chinese captioning for images / video scenes (M1-5).

For an image: produce one Chinese natural-language description. For a video:
read ``basic.scene_detect`` scenes and describe each scene's keyframe, keeping
the scene timestamp so a description can be searched and the player can jump
to the exact second.

The description backend has two implementations behind the same contract,
mirroring ``asr.faster_whisper``:

* **real** — a Qwen3-VL endpoint exposing an OpenAI-compatible
  ``/chat/completions`` interface (vLLM / Ollama / LM Studio). The image is
  sent as a base64 data URL. Failing endpoint / timeout / bad payload yields
  ``None`` so ``backend="auto"`` can fall back to mock.
* **mock** — a deterministic pseudo-caption seeded by the asset identity, the
  timestamp, and coarse colour statistics. This is the default on a vanilla
  install so the caption schema, the caption-scope vectors, and the search
  path can be exercised end-to-end before a real endpoint is configured.

Output shape: ``{"status", "backend", "captions": [{"t", "caption"}]}`` where
``t`` is the scene keyframe in seconds (``null`` for whole images).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import urllib.request
from typing import Any, Optional

import numpy as np
from pydantic import BaseModel

from hometrove.plugins.api import (
    AssetLike,
    Cost,
    MediaType,
    PluginContext,
    resolve_asset_path,
)
from hometrove.plugins.base import BasePlugin

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock backend: deterministic pseudo-captions.
# ---------------------------------------------------------------------------

_MOCK_TEMPLATES = (
    "这是一张记录生活瞬间的照片，画面{light}，{subject}清晰可见，氛围宁静而温馨。",
    "照片捕捉了{scene}的一刻，色彩{tones}，细节丰富，值得珍藏。",
    "画面以{tones}为主调，{subject}占据视觉中心，构图简洁，观感舒适。",
    "影像呈现了{scene}，光线{light}，整体氛围自然，是家庭记忆的一部分。",
    "这张照片展现了{scene}，{subject}被柔和地框入画面，色调{tones}，充满生活气息。",
)

_MOCK_SUBJECTS = ("主体", "人物", "建筑轮廓", "花草树木", "近景物体", "远方景色", "居中构图对象")
_MOCK_SCENES = ("室内角落", "户外庭院", "街边一景", "开阔的风景", "聚会现场", "日常场景", "旅途见闻")
_MOCK_LIGHTS = ("明亮通透", "柔和", "略显昏暗", "反差明显")
_MOCK_TONES = ("温暖", "清冷", "自然", "偏暖的黄调")


def _mock_caption(asset: AssetLike, t: Optional[float], img: Any) -> str:
    """Deterministic caption seeded by asset identity + timestamp + colours."""
    seed = f"{asset.id}:{asset.content_hash_prefix or asset.path}"
    if t is not None:
        seed = f"{seed}:t{t:.3f}"
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)

    tones = "自然"
    light = "柔和"
    if img is not None and getattr(img, "shape", None):
        try:
            arr = np.asarray(img, dtype=np.float32)
            mean = arr.mean(axis=(0, 1))  # R, G, B
            brightness = float(mean.mean()) / 255.0
            warm = float(mean[0]) - float(mean[2])  # R - B
            if warm > 12:
                tones = "温暖"
            elif warm < -12:
                tones = "清冷"
            if brightness < 0.35:
                light = "略显昏暗"
            elif brightness > 0.7:
                light = "明亮通透"
            else:
                light = _MOCK_LIGHTS[h % len(_MOCK_LIGHTS)]
        except Exception:  # noqa: BLE001  — any colour-stat failure -> defaults
            pass

    return (
        _MOCK_TEMPLATES[h % len(_MOCK_TEMPLATES)]
        .replace("{light}", light)
        .replace("{tones}", tones)
        .replace("{subject}", _MOCK_SUBJECTS[(h // 3) % len(_MOCK_SUBJECTS)])
        .replace("{scene}", _MOCK_SCENES[(h // 5) % len(_MOCK_SCENES)])
    )


# ---------------------------------------------------------------------------
# Real backend: OpenAI-compatible VLM endpoint.
# ---------------------------------------------------------------------------


def _img_to_b64_data_url(img: Any) -> Optional[str]:
    """JPEG-encode an RGB numpy frame into a ``data:image/jpeg;base64,...`` URL."""
    try:
        from PIL import Image

        im = Image.fromarray(np.asarray(img)).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001  — undecodable frame -> None
        return None


def _describe_real(
    *,
    endpoint_url: str,
    model: str,
    prompt: str,
    image_b64: str,
    max_tokens: int,
    temperature: float,
) -> Optional[str]:
    """Call the VLM endpoint and return the caption text, or None on failure."""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_b64}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        endpoint_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — operator-configured endpoint
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        text = (content or "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001  — network / timeout / bad response
        log.warning("vlm.qwen3vl: real endpoint call failed: %s", exc)
        return None


# Marker used by tests to flip backend availability without a live endpoint.
_HAS_QWEN3VL: bool | None = None


def has_qwen3vl() -> bool:
    """Endpoint availability probe.

    The real backend is "available" only when an endpoint URL is configured;
    nothing is probed eagerly so a vanilla install never dials the network.
    Tests can set ``_HAS_QWEN3VL`` to simulate availability.
    """
    global _HAS_QWEN3VL
    if _HAS_QWEN3VL is not None:
        return _HAS_QWEN3VL
    return False


def set_qwen3vl_available(available: bool) -> None:
    """Test seam: pin the availability probe."""
    global _HAS_QWEN3VL
    _HAS_QWEN3VL = available


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class VlmQwen3vlPlugin(BasePlugin):
    id: str = "vlm.qwen3vl"
    name: str = "中文描述（Qwen3-VL）"
    description: str = "图片输出一句中文描述；视频按场景输出多条带时间戳的中文描述，用于语义搜索"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    # Videos need scene boundaries before per-scene captions; scene_detect is
    # ``skipped`` for images, which the dependency check treats as satisfied.
    depends_on: list[str] = ["basic.info", "basic.scene_detect"]
    # The real backend requires an operator-configured Qwen3-VL endpoint; a
    # vanilla install falls back to the deterministic mock. Since the mock
    # is the default, the plugin is treated as stub until an endpoint is set.
    category: str = "stub"

    class ParamsModel(BaseModel):
        prompt: str = "请用中文描述这张图片，突出主体、场景、动作、显著细节。"
        max_tokens: int = 256
        temperature: float = 0.2
        # ``backend`` lets the operator pin a backend regardless of whether a
        # real endpoint is configured. ``auto`` prefers the real endpoint when
        # available and falls back to mock; ``mock`` forces the synthetic path;
        # ``qwen3vl`` requires the endpoint.
        backend: str = "auto"
        endpoint_url: Optional[str] = None   # OpenAI-compatible /chat/completions URL
        endpoint_model: str = "qwen3-vl"
        max_scenes: int = 24                # cap captions per video

    def estimate(self, asset: AssetLike) -> Cost:
        # Heuristic: ~1.8 s of VLM time per scene, ~0.6 s per image. Real
        # endpoint calls dominate; mock is effectively free. CPU device is
        # reported so the cost ledger is meaningful on CPU-only installs.
        if asset.media_type == MediaType.VIDEO.value:
            return Cost(seconds=2.0, device="cpu")
        return Cost(seconds=0.6, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: VlmQwen3vlPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        if asset.media_type not in (MediaType.IMAGE.value, MediaType.VIDEO.value):
            return {"status": "skipped", "reason": "not an image or video asset"}

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        try:
            if asset.media_type == MediaType.VIDEO.value:
                return self._caption_video(asset, ctx, params)
            return self._caption_image(asset, ctx, params)
        except Exception as exc:  # noqa: BLE001  — decode/endpoint failure -> skip
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

    def _caption_image(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        params: Any,
    ) -> dict[str, Any]:
        img = ctx.image()
        if img is None:
            return {"status": "skipped", "reason": "cannot decode image"}
        caption, backend = self._describe(asset, None, img, params)
        if caption is None:
            return {"status": "skipped", "reason": "VLM unavailable (configure endpoint_url)"}
        ctx.report_progress(1.0, "captioned 1/1")
        return {
            "status": "ok",
            "backend": backend,
            "captions": [{"t": None, "caption": caption}],
        }

    def _caption_video(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        params: Any,
    ) -> dict[str, Any]:
        scenes: list[dict[str, Any]] = []
        data = ctx.result_of("basic.scene_detect")
        if data:
            scenes = data.get("scenes") or []
        scenes = scenes[: params.max_scenes]

        times = [
            float(s.get("keyframe", (s.get("start", 0.0) + s.get("end", 0.0)) / 2))
            for s in scenes
        ]
        if not times:
            times = [0.0]  # whole video as a single window

        frames = ctx.frames(at_seconds=times)
        if not frames:
            return {"status": "skipped", "reason": "video decode returned no frames"}

        out: list[dict[str, Any]] = []
        backend_used: Optional[str] = None
        for i, (ts, frame) in enumerate(zip(times, frames)):
            if frame is None:
                continue
            caption, backend = self._describe(asset, ts, frame, params)
            if caption is None:
                if backend_used is None:
                    return {"status": "skipped", "reason": "VLM unavailable (configure endpoint_url)"}
                continue
            backend_used = backend
            out.append({"t": round(ts, 3), "caption": caption})
            ctx.report_progress(0.1 + 0.9 * (i + 1) / max(len(times), 1), f"captioned {i+1}/{len(times)}")

        if not out:
            return {"status": "skipped", "reason": "VLM unavailable (configure endpoint_url)"}
        return {"status": "ok", "backend": backend_used, "captions": out}

    def _describe(
        self,
        asset: AssetLike,
        t: Optional[float],
        img: Any,
        params: Any,
    ) -> tuple[Optional[str], str]:
        """Return ``(caption, backend)``; caption is None when the chosen
        backend cannot produce text (auto then falls back to mock)."""
        backend = (params.backend or "auto").lower()
        if backend in ("qwen3vl", "auto") and has_qwen3vl() and params.endpoint_url:
            url = (params.endpoint_url or "").strip()
            b64 = _img_to_b64_data_url(img)
            if url and b64:
                caption = _describe_real(
                    endpoint_url=url,
                    model=params.endpoint_model or "qwen3-vl",
                    prompt=params.prompt,
                    image_b64=b64,
                    max_tokens=params.max_tokens,
                    temperature=params.temperature,
                )
                if caption is not None:
                    return caption, "qwen3vl"
                if backend == "qwen3vl":
                    return None, "qwen3vl"
                # auto: fall through to mock
        if backend == "qwen3vl":
            return None, "qwen3vl"
        return _mock_caption(asset, t, img), "mock"


__all__ = ["VlmQwen3vlPlugin"]
