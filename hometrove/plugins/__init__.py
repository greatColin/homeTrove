"""Plugin API surface for HomeTrove.

M0 only exposes what ``basic.info`` needs. The interface is the same one
described in the project README §8.2, narrowed to the minimum sufficient set.
"""

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin
from hometrove.plugins.registry import REGISTRY, get_plugin, list_plugins, register

__all__ = [
    "AssetLike",
    "BasePlugin",
    "Cost",
    "MediaType",
    "PluginContext",
    "REGISTRY",
    "get_plugin",
    "list_plugins",
    "register",
]
