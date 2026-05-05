"""Load TOML strategy profiles and build plugin registries."""

from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

from zenquant.plugins.registry import PluginRegistry, load_registry


def load_profile(profile_path: Path) -> dict[str, Any]:
    """Parse a TOML strategy profile into a plain dict."""
    with profile_path.open("rb") as f:
        return tomllib.load(f)


def build_from_profile(
    profile_path: Path,
    plugin_root: Path | None = None,
    config_overrides: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], PluginRegistry]:
    """Load profile + build registry in one call.

    Returns:
        (profile_dict, registry) tuple ready for engine integration.
    """
    profile = load_profile(profile_path)
    if config_overrides:
        profile = deepcopy(profile)
        for section_key, section_overrides in config_overrides.items():
            section = profile.setdefault(section_key, {})
            for plugin_name, plugin_overrides in section_overrides.items():
                plugin_config = section.setdefault(plugin_name, {})
                plugin_config.update(plugin_overrides)
    registry = load_registry(profile, plugin_root=plugin_root)
    return profile, registry
