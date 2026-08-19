"""BasePlugin — the contract plugins implement.

This is intentionally narrow in M0. It is the *same* name and shape as the
production contract in README §8.2, so plugins written now will continue to
load when M1 expands the registry.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, PluginContext


class EmptyParams(BaseModel):
    """Default params model for plugins that expose no knobs."""


class BasePlugin:
    """All HomeTrove plugins extend this.

    Subclasses MUST override ``id``, ``version`` and ``run``. The orchestrator
    uses ``depends_on`` / ``supported_media`` for scheduling; ``estimate``
    drives progress weighting.
    """

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""   # one-line description shown in plugin settings
    version: ClassVar[str] = "0.1.0"
    supported_media: ClassVar[set[str]] = set()
    depends_on: ClassVar[list[str]] = []

    # If a subclass needs to expose user-tunable params it sets ParamsModel.
    # Default is a concrete empty model so ``model_validate({})`` always works.
    ParamsModel: ClassVar[type[BaseModel]] = EmptyParams

    # ----- overridable hooks -----

    def estimate(self, asset: AssetLike) -> Cost:  # noqa: ARG002
        return Cost(seconds=0.0, device="cpu")

    def startup(self) -> None:
        """Initialize process-level resources held by this plugin.

        Called when the plugin is enabled through the settings API or at
        application startup if the plugin is already enabled. Default is a
        no-op; plugins that lazily load heavy artifacts (models, pools) should
        implement this hook. Any exception raised here flips the plugin status
        to ``error``.
        """

    def shutdown(self) -> None:
        """Release any process-level resources held by this plugin.

        Called when the plugin is disabled through the settings API. Default
        is a no-op; plugins that cache heavy artifacts across calls (models,
        pools) should free them here. Only in-memory resources are released —
        on-disk artifacts (thumbs, results) are intentionally kept.
        """

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        raise NotImplementedError

    # ----- helpers -----

    def is_compatible(self, asset: AssetLike) -> bool:
        return asset.media_type in self.supported_media
