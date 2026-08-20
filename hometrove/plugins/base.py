"""BasePlugin — the contract plugins implement.

This is intentionally narrow in M0. It is the *same* name and shape as the
production contract in README §8.2, so plugins written now will continue to
load when M1 expands the registry.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, PluginContext


class EmptyParams(BaseModel):
    """Default params model for plugins that expose no knobs."""


# A plugin's "category" reflects whether it actually computes results from
# the original file/media, or produces placeholder / hash-based output. The
# UI groups plugins by this field so operators can tell implemented work
# from stubs that exist only to feed the frontend.
#
# - "implemented" reads the file and returns real analysis output.
# - "stub" produces deterministic placeholder data, OR depends (transitively)
#   on a plugin that does. The label is conservative: anything that touches
#   fake data is also "stub", even if it has a real implementation, because
#   its real path is gated on a stub upstream.
#
# Plugins inherit the worst category in their dependency chain: a plugin
# with no explicit override that depends on a stub will itself be reported
# as stub by ``effective_category``.
PluginCategoryT = Literal["implemented", "stub"]


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
    # Override in stub plugins. Left unset (=None) so ``effective_category``
    # can derive it from the dependency chain.
    category: ClassVar[PluginCategoryT | None] = None

    # Heavy-startup plugins (e.g. face.image / face.video boot-load a ~250MB
    # onnx pack on ``startup()``) set this True so the lifecycle manager
    # starts them on a background thread — the app becomes reachable
    # immediately and the plugin flips loading → active in the background.
    heavy_startup: ClassVar[bool] = False

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

    def effective_category(self, registry: Any) -> PluginCategoryT:
        """Return ``"stub"`` if this plugin or any of its ``depends_on``
        ancestors is a stub, else ``"implemented"``.

        ``registry`` is the ``PluginRegistry`` instance; we accept it as a
        parameter so the helper stays a method, not a global, and so tests
        can inject a fake registry without monkey-patching imports.
        """
        if self.category == "stub":
            return "stub"
        if self.category == "implemented":
            return "implemented"
        # category is unset; derive from the dependency chain so adding a new
        # stub doesn't require manually re-tagging every downstream plugin.
        for dep_id in self.depends_on:
            dep = registry.get(dep_id) if hasattr(registry, "get") else None
            if dep is None:
                continue
            if getattr(dep, "effective_category", lambda _: "implemented")(registry) == "stub":
                return "stub"
        return "implemented"
