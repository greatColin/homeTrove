"""Mock plugins for UI development.

These generate deterministic, plausible values for tag / category / face data
so the frontend pages (asset detail, tags, categories, faces) can be developed
and tested before the real plugins land. The output shape follows the schema
the real plugins will produce in M1.

Each plugin seeds its randomness from the asset id + content-hash prefix, so
re-running is idempotent (same asset -> same output).
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin

_POOL = {
    "tags": [
        "风景", "人像", "聚会", "旅行", "美食", "宠物", "城市", "夜景",
        "日出", "日落", "海边", "山景", "家人", "朋友", "孩子", "节日",
        "建筑", "公园", "运动", "街拍",
    ],
    "categories": ["风景", "人像", "生活记录", "旅行", "美食", "宠物", "建筑", "活动"],
    "persons": [
        "张三", "李四", "王五", "赵六", "陈七", "刘八",
    ],
}


def _seed(asset: AssetLike) -> int:
    raw = f"{asset.id}:{asset.content_hash_prefix or asset.path}"
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16)


def _pick(seed: int, pool: list[str], count: int) -> list[str]:
    # Deterministic selection spread across the pool.
    out: list[str] = []
    for i in range(count):
        idx = (seed >> (i * 7)) % len(pool)
        v = pool[idx]
        if v not in out:
            out.append(v)
    return out


_EMBEDDING_DIM = 64


def _identity_embedding(identity: int, seed: int) -> list[float]:
    """Deterministic normalized vector for a simulated person identity.

    Same ``identity`` yields near-identical vectors across different assets;
    ``seed`` (asset-specific) adds small noise so the matcher sees realistic
    within-identity similarity rather than exact equality.
    """
    import math

    # Anchor vector for the identity — derived only from ``identity``.
    anchor = []
    for d in range(_EMBEDDING_DIM):
        h = hashlib.sha256(f"id{identity}:d{d}".encode()).digest()
        anchor.append(int.from_bytes(h[:8], "big") / 2**64 * 2.0 - 1.0)
    # Noise proportional to seed.
    noise = []
    for d in range(_EMBEDDING_DIM):
        h = hashlib.sha256(f"n{seed}:d{d}".encode()).digest()
        noise.append((int.from_bytes(h[:8], "big") / 2**64 - 0.5) * 0.2)
    vec = [a + no for a, no in zip(anchor, noise)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


class MockTagsPlugin(BasePlugin):
    id: str = "mock.tags"
    name: str = "标签（模拟）"
    description: str = "（模拟占位）为图片/视频生成随机标签，用于前端标签页演示；真实标签由 vlm.qwen3vl 提供"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        max_tags: int = 4

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.001, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: MockTagsPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        n = min(params.max_tags, 1 + (_seed(asset) % params.max_tags))
        return {"tags": _pick(_seed(asset), _POOL["tags"], n)}


class MockCategoryPlugin(BasePlugin):
    id: str = "mock.category"
    name: str = "分类（模拟）"
    description: str = "（模拟占位）为图片/视频生成随机分类，用于前端分类页演示；真实分类由 AI 模型提供"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        max_categories: int = 2

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.001, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: MockCategoryPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        cats = _pick(_seed(asset), _POOL["categories"], params.max_categories)
        return {
            "category": cats[0],
            "subcategory": cats[1] if len(cats) > 1 else None,
            "confidence": 0.5 + (_seed(asset) % 50) / 100.0,
        }


class MockFacesPlugin(BasePlugin):
    id: str = "mock.faces"
    name: str = "人脸检测（模拟）"
    description: str = "（模拟占位）为图片生成随机人脸向量，用于前端人脸页演示；真实人脸由 face.detect 提供"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        max_faces: int = 3

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.001, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        """Emit face embeddings only.

        A real detector returns a vector per face. To make the matcher
        demonstrable, we simulate a few stable "identities": each identity has
        an anchor vector, and every face drawn from it is the anchor plus
        small deterministic noise — so faces of the same identity are similar
        and cross-photo grouping actually works.
        """
        params: MockFacesPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        seed = _seed(asset)
        n = seed % (params.max_faces + 1)  # 0..max_faces
        faces: list[dict[str, Any]] = []
        for i in range(n):
            identity = (seed >> (i * 5)) % 6  # 0..5, deterministic per face
            vec = _identity_embedding(identity, seed + i * 17)
            faces.append(
                {
                    "embedding": vec,
                    "confidence": round(0.6 + ((seed >> i) % 35) / 100.0, 3),
                    "box": [10 + (seed >> i) % 100, 10 + (seed >> (i + 3)) % 100],
                }
            )
        return {"faces": faces}


def _register_mocks() -> None:
    from hometrove.plugins.registry import REGISTRY as _R
    _R.register(MockTagsPlugin())
    _R.register(MockCategoryPlugin())
    _R.register(MockFacesPlugin())


_register_mocks()

__all__ = ["MockTagsPlugin", "MockCategoryPlugin", "MockFacesPlugin"]
