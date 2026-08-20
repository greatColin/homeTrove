"""Built-in plugins for M0.

Currently exports only ``BasicInfoPlugin``. New built-ins land in M1 and must
not be added here without an RFC.
"""

from hometrove.plugins.builtin.basic_info import BasicInfoPlugin
from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin
from hometrove.plugins.builtin.embedding_bge_m3 import EmbeddingBgeM3Plugin
from hometrove.plugins.builtin.embedding_clip import EmbeddingJinaClipPlugin
from hometrove.plugins.builtin.exif import ExifPlugin
from hometrove.plugins.builtin.face_detect import FaceDetectPlugin
from hometrove.plugins.builtin.face_image import FaceImagePlugin
from hometrove.plugins.builtin.face_video import FaceVideoPlugin
from hometrove.plugins.builtin.keyframes import KeyframesPlugin
from hometrove.plugins.builtin.scene_detect import SceneDetectPlugin
from hometrove.plugins.builtin.thumbnail import ThumbnailPlugin
from hometrove.plugins.builtin.vlm_qwen3vl import VlmQwen3vlPlugin

__all__ = [
    "AsrFasterWhisperPlugin",
    "BasicInfoPlugin",
    "EmbeddingBgeM3Plugin",
    "EmbeddingJinaClipPlugin",
    "ExifPlugin",
    "FaceDetectPlugin",
    "FaceImagePlugin",
    "FaceVideoPlugin",
    "KeyframesPlugin",
    "SceneDetectPlugin",
    "ThumbnailPlugin",
    "VlmQwen3vlPlugin",
]


def _register_builtins() -> None:
    from hometrove.plugins.registry import REGISTRY as _R
    _R.register(AsrFasterWhisperPlugin())
    _R.register(BasicInfoPlugin())
    _R.register(EmbeddingBgeM3Plugin())
    _R.register(EmbeddingJinaClipPlugin())
    _R.register(ExifPlugin())
    _R.register(FaceDetectPlugin())
    _R.register(FaceImagePlugin())
    _R.register(FaceVideoPlugin())
    _R.register(KeyframesPlugin())
    _R.register(SceneDetectPlugin())
    _R.register(ThumbnailPlugin())
    _R.register(VlmQwen3vlPlugin())
    # Mock plugins feed the frontend tag / category / face pages with
    # deterministic sample data. Real plugins replace them in M1.
    from hometrove.plugins.mock import MockTagsPlugin, MockCategoryPlugin, MockFacesPlugin
    _R.register(MockTagsPlugin())
    _R.register(MockCategoryPlugin())
    _R.register(MockFacesPlugin())
    # Real matching pipeline that groups detected face vectors under people.
    from hometrove.plugins.face_match import FaceMatchPlugin
    _R.register(FaceMatchPlugin())


_register_builtins()
