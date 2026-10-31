"""Verifiable semantic identity for the runtime portfolio-accounting kernel."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

ACCOUNTING_KERNEL_IDENTITY_SCHEMA_VERSION = "accounting-kernel-identity.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DOMAIN_ACCOUNTING_PATH = Path("zenquant/domain/accounting.py")
_RISK_GUARD_PATH = Path("zenquant/risk/guard.py")
_EXECUTION_ROUTER_PATH = Path("zenquant/execution/router.py")
_RISK_METHODS = (
    "_on_mark_price",
    "prime_indicator",
    "_on_execution_report",
    "apply_execution_report",
    "apply_fee",
    "_revalue_equity",
)
_EXECUTION_ACCOUNTING_HOOK = "_apply_report_accounting"


def build_accounting_kernel_identity(
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    """Hash normalized AST for every runtime transition that books or marks equity."""
    root = repository_root.resolve()
    components: list[dict[str, str]] = []

    domain_tree = _parse_module(root / _DOMAIN_ACCOUNTING_PATH)
    components.append(_component(_DOMAIN_ACCOUNTING_PATH, "<module>", domain_tree))

    risk_tree = _parse_module(root / _RISK_GUARD_PATH)
    risk_class = _required_class(risk_tree, "RiskOverlay", path=_RISK_GUARD_PATH)
    for method_name in _RISK_METHODS:
        _required_method(risk_class, method_name, path=_RISK_GUARD_PATH)
    components.append(_component(_RISK_GUARD_PATH, "<module>", risk_tree))

    execution_tree = _parse_module(root / _EXECUTION_ROUTER_PATH)
    execution_class = _required_class(execution_tree, "ExecutionKernel", path=_EXECUTION_ROUTER_PATH)
    _required_method(execution_class, _EXECUTION_ACCOUNTING_HOOK, path=_EXECUTION_ROUTER_PATH)
    components.append(_component(_EXECUTION_ROUTER_PATH, "<module>", execution_tree))

    components.sort(key=lambda item: item["identity"])
    payload = {
        "schema_version": ACCOUNTING_KERNEL_IDENTITY_SCHEMA_VERSION,
        "components": components,
    }
    return {
        **payload,
        "sha256": f"sha256:{hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()}",
    }


def _parse_module(path: Path) -> ast.Module:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to read accounting kernel source:{path}") from exc
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"unable to parse accounting kernel source:{path}") from exc


def _required_class(tree: ast.Module, name: str, *, path: Path) -> ast.ClassDef:
    node = next((item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name), None)
    if node is None:
        raise ValueError(f"accounting kernel component missing:{path}::{name}")
    return node


def _required_method(class_node: ast.ClassDef, name: str, *, path: Path) -> ast.AST:
    node = next(
        (
            item
            for item in class_node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name
        ),
        None,
    )
    if node is None:
        raise ValueError(f"accounting kernel component missing:{path}::{class_node.name}.{name}")
    return node


def _component(path: Path, qualname: str, node: ast.AST) -> dict[str, str]:
    semantic_ast = ast.dump(node, annotate_fields=True, include_attributes=False)
    return {
        "identity": f"{path.as_posix()}::{qualname}",
        "semantic_sha256": f"sha256:{hashlib.sha256(semantic_ast.encode('utf-8')).hexdigest()}",
    }


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
