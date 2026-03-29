"""Gate B: Import boundary enforcement.

Architectural boundaries prevent entropy. Plugins must depend only on
protocols.py — never on domain types, strategy internals, or infra.
Strategy must not reach into app or execution.

These rules are mechanical: they cannot rot like documentation.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PLUGIN_IMPL_DIRS = [
    PROJECT_ROOT / "zenquant" / "plugins" / "signals",
    PROJECT_ROOT / "zenquant" / "plugins" / "filters",
    PROJECT_ROOT / "zenquant" / "plugins" / "sizers",
    PROJECT_ROOT / "zenquant" / "plugins" / "exits",
]

FORBIDDEN_PLUGIN_IMPORTS = [
    "zenquant.domain",
    "zenquant.strategy",
    "zenquant.infra",
    "zenquant.state",
    "zenquant.indicator",
    "zenquant.execution",
    "zenquant.risk",
    "zenquant.app",
    "zenquant.historical",
    "zenquant.research",
]

STRATEGY_DIR = PROJECT_ROOT / "zenquant" / "strategy"

FORBIDDEN_STRATEGY_IMPORTS = [
    "zenquant.app",
    "zenquant.execution",
]


def _collect_imports(filepath: Path) -> list[str]:
    """Extract all import module paths from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def _check_boundary(
    source_dirs: list[Path],
    forbidden_prefixes: list[str],
    label: str,
) -> list[str]:
    """Return list of violation descriptions."""
    violations: list[str] = []
    for src_dir in source_dirs:
        if not src_dir.exists():
            continue
        for py_file in src_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            imports = _collect_imports(py_file)
            for imp in imports:
                for forbidden in forbidden_prefixes:
                    if imp == forbidden or imp.startswith(forbidden + "."):
                        rel = py_file.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"{label}: {rel} imports '{imp}' (forbidden: {forbidden})"
                        )
    return violations


class TestImportBoundaries(unittest.TestCase):
    """Mechanical enforcement of module boundaries."""

    def test_plugin_implementations_only_import_protocols(self):
        """Plugin signal/filter/sizer/exit files must not import zenquant internals."""
        violations = _check_boundary(
            PLUGIN_IMPL_DIRS,
            FORBIDDEN_PLUGIN_IMPORTS,
            "PLUGIN",
        )
        if violations:
            msg = "\n".join(["Import boundary violations:"] + violations)
            self.fail(msg)

    def test_strategy_does_not_import_app_or_execution(self):
        """Strategy module must not reach into app or execution."""
        violations = _check_boundary(
            [STRATEGY_DIR],
            FORBIDDEN_STRATEGY_IMPORTS,
            "STRATEGY",
        )
        if violations:
            msg = "\n".join(["Import boundary violations:"] + violations)
            self.fail(msg)

    def test_indicator_does_not_import_strategy(self):
        """Indicator engine must not depend on strategy."""
        indicator_dir = PROJECT_ROOT / "zenquant" / "indicator"
        violations = _check_boundary(
            [indicator_dir],
            ["zenquant.strategy"],
            "INDICATOR",
        )
        if violations:
            msg = "\n".join(["Import boundary violations:"] + violations)
            self.fail(msg)

    def test_state_does_not_import_strategy(self):
        """State engine must not depend on strategy."""
        state_dir = PROJECT_ROOT / "zenquant" / "state"
        violations = _check_boundary(
            [state_dir],
            ["zenquant.strategy"],
            "STATE",
        )
        if violations:
            msg = "\n".join(["Import boundary violations:"] + violations)
            self.fail(msg)


if __name__ == "__main__":
    unittest.main()
