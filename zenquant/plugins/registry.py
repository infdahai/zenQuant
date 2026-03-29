"""Discover, load, and validate strategy plugins from the filesystem.

Plugins are loaded at startup — not lazily — so import failures surface
immediately.  Each plugin is a ``.py`` file exposing module-level ``meta``
(a :class:`PluginMeta`) and ``compute`` (a callable matching the plugin's
protocol signature).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from zenquant.plugins.protocols import PluginMeta

PLUGIN_TYPES = ("signal", "filter", "sizer", "exit")
PLUGIN_DIR_MAP = {
    "signal": "signals",
    "filter": "filters",
    "sizer": "sizers",
    "exit": "exits",
}


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """A validated, ready-to-call plugin with its merged config."""

    meta: PluginMeta
    compute: Callable[..., Any]
    config: dict[str, Any]
    weight: float  # signal weight or exit priority
    source_path: str


@dataclass(slots=True)
class PluginRegistry:
    """Holds all active plugins for one strategy profile."""

    signals: list[LoadedPlugin] = field(default_factory=list)
    filters: list[LoadedPlugin] = field(default_factory=list)
    sizers: list[LoadedPlugin] = field(default_factory=list)
    exits: list[LoadedPlugin] = field(default_factory=list)
    _index: dict[str, LoadedPlugin] = field(default_factory=dict)

    def get(self, name: str) -> LoadedPlugin | None:
        return self._index.get(name)

    def names(self) -> list[str]:
        return list(self._index.keys())

    def summary(self) -> dict[str, list[str]]:
        return {
            "signals": [p.meta.name for p in self.signals],
            "filters": [p.meta.name for p in self.filters],
            "sizers": [p.meta.name for p in self.sizers],
            "exits": [p.meta.name for p in self.exits],
        }

    @property
    def signal_weights(self) -> dict[str, float]:
        return {p.meta.name: p.weight for p in self.signals}


def load_registry(
    profile: dict[str, Any],
    plugin_root: Path | None = None,
) -> PluginRegistry:
    """Build a registry from a parsed TOML profile dict.

    Expected profile structure::

        {
            "signals": {"momentum_signal": {"enabled": true, "weight": 0.4, ...}},
            "filters": {"quality_filter": {"enabled": true, ...}},
            ...
        }
    """
    if plugin_root is None:
        plugin_root = Path(__file__).parent

    registry = PluginRegistry()

    for plugin_type in PLUGIN_TYPES:
        section_key = f"{plugin_type}s"
        section = profile.get(section_key, {})
        dir_name = PLUGIN_DIR_MAP[plugin_type]
        plugin_dir = plugin_root / dir_name

        for plugin_name, plugin_conf in section.items():
            if not isinstance(plugin_conf, dict):
                continue
            if not plugin_conf.get("enabled", True):
                continue

            loaded = _load_one(
                plugin_name=plugin_name,
                plugin_type=plugin_type,
                plugin_dir=plugin_dir,
                config_overrides=plugin_conf,
            )
            target_list = getattr(registry, section_key)
            target_list.append(loaded)
            registry._index[plugin_name] = loaded

    # Sort exits by priority (lower = higher priority)
    registry.exits.sort(key=lambda p: p.weight)

    return registry


# ── Reserved keys that are handled by the registry, not forwarded to plugins ──
_RESERVED_KEYS = frozenset({"enabled", "weight", "priority"})


def _load_one(
    plugin_name: str,
    plugin_type: str,
    plugin_dir: Path,
    config_overrides: dict[str, Any],
) -> LoadedPlugin:
    """Load a single plugin module, validate it, and merge config."""
    plugin_path = plugin_dir / f"{plugin_name}.py"
    if not plugin_path.exists():
        raise FileNotFoundError(f"Plugin file not found: {plugin_path}")

    module_name = f"zenquant.plugins.{PLUGIN_DIR_MAP[plugin_type]}.{plugin_name}"

    # Allow hot-reload during AutoResearch by removing stale module
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, str(plugin_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load plugin: {plugin_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "meta"):
        raise AttributeError(f"Plugin '{plugin_name}' missing 'meta' attribute")
    if not hasattr(module, "compute"):
        raise AttributeError(f"Plugin '{plugin_name}' missing 'compute' function")

    meta: PluginMeta = module.meta
    if meta.plugin_type != plugin_type:
        raise ValueError(
            f"Plugin '{plugin_name}' declares type '{meta.plugin_type}' "
            f"but is located in '{plugin_type}s/' directory"
        )

    # Build config: schema defaults merged with profile overrides
    config: dict[str, Any] = {}
    if meta.config_schema is not None:
        defaults = meta.config_schema()
        config = {
            k: getattr(defaults, k) for k in defaults.__dataclass_fields__
        }
    for k, v in config_overrides.items():
        if k not in _RESERVED_KEYS:
            config[k] = v

    # Weight: for signals it's combination weight, for exits it's priority
    if plugin_type == "exit":
        weight = float(config_overrides.get("priority", 99))
    else:
        weight = float(config_overrides.get("weight", 1.0))

    return LoadedPlugin(
        meta=meta,
        compute=module.compute,
        config=config,
        weight=weight,
        source_path=str(plugin_path.resolve()),
    )
