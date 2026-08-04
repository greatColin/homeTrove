"""Plugin API surface for HomeTrove.

M0 only exposes what ``basic.info`` needs. The interface is the same one
described in the project README §8.2, narrowed to the minimum sufficient set.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    OTHER = "other"


class Cost(BaseModel):
    seconds: float = 0.0
    device: str = "cpu"   # 'cpu' | 'gpu' | 'any'

    def __mul__(self, factor: float) -> "Cost":
        return Cost(seconds=self.seconds * factor, device=self.device)


class AssetLike(BaseModel):
    """A description of a discovered asset passed to a plugin."""

    id: int
    path: str
    media_root: str
    media_type: str
    size_bytes: Optional[int] = None
    mtime: Optional[int] = None
    content_hash_prefix: Optional[str] = None


class PluginContext:
    """Per-call context.

    ``params`` is the resolved ``ParamsModel`` instance; ``report_progress``
    is a no-op in M0 so plugins can call it unconditionally.
    """

    def __init__(self, asset: AssetLike, params: Any) -> None:
        self.asset = asset
        self._params = params

    @property
    def params(self) -> Any:
        return self._params

    def report_progress(self, frac: float, msg: str = "") -> None:  # noqa: ARG002
        return None
