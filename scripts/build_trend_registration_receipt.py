"""Dependency-free CLI for trusted trend-research registration receipts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _load_registration_contracts() -> Any:
    """Load the stdlib-only registration module without importing its package."""

    module_name = "_trend_registration_contracts"
    module_path = Path(__file__).resolve().parent.parent / "zenquant/research/registration.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load registration contracts: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[module_name]
        raise
    return module


_CONTRACTS = _load_registration_contracts()
RegistrationError = _CONTRACTS.RegistrationError


def _load_provider_run(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot load provider run payload: {path}") from exc
    if not isinstance(payload, dict):
        raise RegistrationError("provider run payload must be a JSON object")
    return payload


def _write_selection(github_env: Path, selection: tuple[str, str]) -> None:
    manifest_kind, manifest_path = selection
    with github_env.open("a", encoding="utf-8") as output:
        output.write(f"MANIFEST_KIND={manifest_kind}\nMANIFEST_PATH={manifest_path}\n")


def _build_command(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--provider-run-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)


def _selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest-kind", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--github-env", type=Path, required=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    _build_command(build)
    bootstrap = commands.add_parser("select-bootstrap")
    bootstrap.add_argument("--branch-name", required=True)
    bootstrap.add_argument("--github-env", type=Path, required=True)
    manual = commands.add_parser("select-manual")
    manual.add_argument("--github-ref", required=True)
    _selection_arguments(manual)
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest-kind", required=True)
    validate.add_argument("--manifest-path", required=True)
    args = parser.parse_args(argv)

    if args.command == "select-bootstrap":
        _write_selection(args.github_env, _CONTRACTS.resolve_bootstrap_registration(args.branch_name))
        return 0
    if args.command == "select-manual":
        _write_selection(
            args.github_env,
            _CONTRACTS.resolve_manual_registration(args.github_ref, args.manifest_kind, args.manifest_path),
        )
        return 0
    if args.command == "validate":
        _CONTRACTS.validate_registration_selection(args.manifest_kind, args.manifest_path)
        return 0

    provider_run = _load_provider_run(args.provider_run_json)
    envelope = _CONTRACTS.build_registration_envelope(args.manifest_path, args.commit_sha, provider_run)
    _CONTRACTS.verify_github_receipt(envelope, provider_run)
    args.output.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
