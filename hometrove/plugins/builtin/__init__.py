"""Built-in plugins for M0.

Currently exports only ``BasicInfoPlugin``. New built-ins land in M1 and must
not be added here without an RFC.
"""

from hometrove.plugins.builtin.basic_info import BasicInfoPlugin

__all__ = ["BasicInfoPlugin"]


def _register_builtins() -> None:
    from hometrove.plugins.registry import REGISTRY as _R
    _R.register(BasicInfoPlugin())
    # Mock plugins feed the frontend tag / category / face pages with
    # deterministic sample data. Real plugins replace them in M1.
    from hometrove.plugins.mock import MockTagsPlugin, MockCategoryPlugin, MockFacesPlugin
    _R.register(MockTagsPlugin())
    _R.register(MockCategoryPlugin())
    _R.register(MockFacesPlugin())


_register_builtins()
