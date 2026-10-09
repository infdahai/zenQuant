"""AST enforcement for every source file, including relative and package imports."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = "zenquant.options"
RESEARCH = "zenquant.research.options_platform"
CORE_STDLIB = {"__future__", "dataclasses", "decimal", "enum", "hashlib", "json", "typing", "collections"}


def _within(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


def _imports(source, *, module, package=False):
    tree = ast.parse(source)  # SyntaxError is deliberately not suppressed.
    context = module.split(".") if package else module.split(".")[:-1]
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(context):
                    raise ValueError("relative import escapes package")
                base = ".".join(context[: len(context) - node.level + 1])
                target = base + ("." + node.module if node.module else "")
            else:
                target = node.module or ""
            imports.append(target)
            imports.extend(target + "." + alias.name for alias in node.names if alias.name != "*")
    return imports


def _violations(source, *, module, package=False):
    result = []
    for target in _imports(source, module=module, package=package):
        if _within(module, CORE):
            if target.split(".")[0] not in CORE_STDLIB and not _within(target, CORE):
                result.append(target)
        elif _within(module, RESEARCH):
            if target.startswith("zenquant") and not (_within(target, CORE) or _within(target, RESEARCH)):
                result.append(target)
        elif _within(target, CORE) or _within(target, RESEARCH):
            result.append(target)
    return result


def test_all_core_research_and_existing_paths_obey_import_boundaries():
    violations = []
    for path in sorted((ROOT / "zenquant").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).with_suffix("")
        package = relative.name == "__init__"
        module = ".".join(relative.parts[:-1] if package else relative.parts)
        for target in _violations(path.read_text(), module=module, package=package):
            violations.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "source,module,package",
    [
        ("from ..risk import guard", "zenquant.options.contracts", False),
        ("from .. import research", CORE, True),
        ("import socket", "zenquant.options.costs", False),
        ("from ..options import contracts", "zenquant.risk.guard", False),
        ("from zenquant.research import options_platform", "zenquant.app.check", False),
        ("from ...strategy import engine", "zenquant.research.options_platform.panel", False),
        ("from .. import options", "zenquant.app", True),
    ],
)
def test_forbidden_imports_cannot_hide_in_relative_or_init(source, module, package):
    assert _violations(source, module=module, package=package)


def test_syntax_error_is_a_hard_failure():
    with pytest.raises(SyntaxError):
        _violations("from . import (", module=CORE, package=True)


def test_allowed_relative_core_and_research_imports():
    assert not _violations("from .contracts import WalletKey", module="zenquant.options.costs")
    assert not _violations("from . import panel", module=RESEARCH, package=True)
    assert not _violations("from zenquant.options import risk", module=RESEARCH, package=True)
