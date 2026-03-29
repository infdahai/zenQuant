"""Load TOML strategy profiles and build plugin registries."""

from __future__ import annotations

import tomllib
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
) -> tuple[dict[str, Any], PluginRegistry]:
    """Load profile + build registry in one call.

    Returns:
        (profile_dict, registry) tuple ready for engine integration.
    """
    profile = load_profile(profile_path)
    registry = load_registry(profile, plugin_root=plugin_root)
    return profile, registry
