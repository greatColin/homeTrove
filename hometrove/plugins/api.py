"""Plugin API surface for HomeTrove.

M0 only exposes what ``basic.info`` needs. The interface is the same one
described in the project README §8.2, narrowed to the minimum sufficient set.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
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


def resolve_asset_path(asset: AssetLike) -> Optional[Path]:
    """Resolve an asset's on-disk file from its ``path`` column.

    Two layouts are supported:
      * scanned media:   ``{media_root}\0{relative}``
      * uploaded media:  ``uploads\0{absolute_staging_path}``
    When the vault is unlocked and the asset is encrypted (Asset DB model with
    an empty path and a non-empty encrypted_path), the decrypted content is
    written to a process-owned temp file and returned; the temp file is
    registered for deletion on interpreter exit so callers do not need to
    manage its lifecycle.
    Returns ``None`` when the file cannot be resolved or is not a regular file.
    """
    raw = asset.path
    if not raw:
        enc_path = getattr(asset, "encrypted_path", None) if hasattr(asset, "encrypted_path") else None
        if enc_path:
            from hometrove.vault import is_unlocked
            if is_unlocked():
                from hometrove.vault.read import read_asset_bytes
                data, _ = read_asset_bytes(asset)
                import tempfile, atexit
                fd, tmp = tempfile.mkstemp(suffix=".bin", prefix=f"hometrove-asset-{asset.id}-")
                os.write(fd, data)
                os.close(fd)
                atexit.register(lambda: Path(tmp).unlink(missing_ok=True))
                return Path(tmp)
        return None
    if "\0" in raw:
        kind, _, rest = raw.partition("\0")
        if kind == "uploads":
            src = Path(rest)
        else:
            src = Path(asset.media_root) / rest
    elif Path(raw).is_absolute():
        src = Path(raw)
    else:
        src = Path(asset.media_root) / raw
    if src.is_file():
        return src
    return None


class PluginContext:
    """Per-call context.

    ``params`` is the resolved ``ParamsModel`` instance; ``report_progress``
    is a no-op in M0 so plugins can call it unconditionally. ``db`` is the
    current SQLAlchemy ``Session`` when the orchestrator supplies one (needed
    by plugins that match against the library, e.g. face.match); it is
    ``None`` in contexts without a database (unit tests, dry runs).
    ``data_dir`` is the resolved runtime data directory, used by plugins that
    write derived artifacts (e.g. thumbnails).

    ``image()`` / ``frames()`` / ``result_of()`` / ``temp_dir()`` form the
    shared-cache surface (M1-5): expensive decodes and upstream plugin reads
    are memoized per-call, so several plugins touching the same asset do not
    each re-decode the file or re-query the database.
    """

    def __init__(self, asset: AssetLike, params: Any, db: Any = None, data_dir: Optional[Path] = None) -> None:
        self.asset = asset
        self._params = params
        self.db = db
        self.data_dir = data_dir
        self._image_cache: dict[tuple, Any] = {}
        self._frames_cache: dict[tuple, list[Any]] = {}
        self._result_cache: dict[str, Optional[dict]] = {}

    @property
    def params(self) -> Any:
        return self._params

    def report_progress(self, frac: float, msg: str = "") -> None:  # noqa: ARG002
        return None

    # ----- shared decode / upstream-read cache (M1-5) -----

    def image(self, *, max_side: Optional[int] = None) -> Any:
        """Decode the asset once and return an RGB numpy array.

        ``max_side`` caps the longest edge (aspect-preserving). Same call
        shape hits the memo; a different ``max_side`` re-decodes.
        """
        import numpy as np

        key = ("image", max_side)
        cached = self._image_cache.get(key)
        if cached is not None:
            return cached

        src = resolve_asset_path(self.asset)
        arr: Any = None
        if src is not None:
            try:
                from PIL import Image

                with Image.open(src) as im:
                    im = im.convert("RGB")
                    if max_side is not None and max(im.size) > max_side:
                        im.thumbnail((max_side, max_side))
                    arr = np.asarray(im)
            except Exception:  # noqa: BLE001  — undecodable => None
                arr = None
        self._image_cache[key] = arr
        return arr

    def frames(
        self,
        *,
        count: int = 8,
        at_seconds: Optional[list[float]] = None,
    ) -> list[Any]:
        """Return up to ``count`` RGB numpy frames for a video asset.

        ``at_seconds`` explicitly selects timestamps (e.g. scene keyframes);
        otherwise ``count`` evenly spaced points are sampled. Memoized per
        ``(count, tuple(at_seconds))`` — repeated calls across plugins reuse
        the decoded frames instead of re-seeking.

        For encrypted assets with vault unlocked, the decrypted bytes are
        written to a temp file so PyAV can read them; the temp file is
        cleaned up after frame extraction.
        """
        import numpy as np

        key = ("frames", count, tuple(at_seconds) if at_seconds is not None else None)
        cached = self._frames_cache.get(key)
        if cached is not None:
            return cached

        src = resolve_asset_path(self.asset)
        tmp_path: Path | None = None
        out: list[Any] = []
        if self.asset.media_type == MediaType.VIDEO.value:
            if src is None:
                from hometrove.vault.read import is_asset_encrypted, read_asset_bytes
                if is_asset_encrypted(self.asset):
                    data = read_asset_bytes(self.asset)
                    if data is not None:
                        import tempfile
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                            f.write(data)
                            tmp_path = Path(f.name)
                        src = tmp_path
            if src is not None:
                try:
                    import av

                    times = at_seconds
                    if times is None:
                        with av.open(str(src)) as container:
                            duration = float(container.duration or 0) / 1_000_000
                        times = [
                            duration * (i + 0.5) / count for i in range(count)
                        ] if duration > 0 else [0.0]

                    with av.open(str(src)) as container:
                        stream = container.streams.video[0]
                        for ts in times:
                            container.seek(int(ts * 1_000_000), stream=stream)
                            for frame in container.decode(video=0):
                                arr = np.asarray(frame.to_ndarray(format="rgb24"))
                                out.append(arr)
                                break
                except Exception:  # noqa: BLE001  — undecodable => []
                    out = []
                finally:
                    if tmp_path is not None and tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
        self._frames_cache[key] = out
        return out

    def result_of(self, plugin_id: str) -> Optional[dict]:
        """Read another plugin's latest output for this asset from the DB.

        Returns ``None`` when the plugin has no successful result (or there is
        no database context). Memoized per ``plugin_id``.
        """
        if plugin_id in self._result_cache:
            return self._result_cache[plugin_id]
        out: Optional[dict] = None
        if self.db is not None:
            import json

            from sqlalchemy import select

            from hometrove.models import PluginResult

            row = self.db.execute(
                select(PluginResult)
                .where(
                    PluginResult.asset_id == self.asset.id,
                    PluginResult.plugin_id == plugin_id,
                    PluginResult.status == "ok",
                )
                .order_by(PluginResult.finished_at.desc())
            ).scalars().first()
            if row is not None:
                try:
                    out = json.loads(row.result_json or "{}")
                except json.JSONDecodeError:
                    out = {}
        self._result_cache[plugin_id] = out
        return out

    def temp_dir(self) -> Path:
        """Per-asset scratch directory under the runtime data dir.

        Created lazily and reused within a single call so plugins can drop
        derived artifacts (extracted audio, staged files) without polluting
        the media root.
        """
        from hometrove.config import get_settings

        base = self.data_dir or get_settings().resolved_data_dir()
        d = Path(base) / "plugin-tmp" / str(self.asset.id)
        d.mkdir(parents=True, exist_ok=True)
        return d
