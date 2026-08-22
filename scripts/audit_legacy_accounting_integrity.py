#!/usr/bin/env python3
"""Write an immutable sidecar describing legacy accounting evidence validity."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.legacy_accounting_validity import (
    DEFAULT_ACCOUNTING_VALIDITY_REGISTRY,
    AccountingValidityRegistryError,
    load_accounting_validity_registry,
)

ACCOUNTING_AUDIT_SCHEMA_VERSION = "legacy-accounting-audit.v1"
_HASH_CHUNK_BYTES = 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--registry", type=Path, default=DEFAULT_ACCOUNTING_VALIDITY_REGISTRY)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_accounting_validity_registry(args.registry)
        source_sha256, source_size_bytes = _source_identity(args.source)
        records = registry.records_for_artifact(str(args.source))
        if records and records[0].source_sha256 != source_sha256:
            raise AccountingValidityRegistryError(
                f"source_sha256_mismatch:{args.source}:expected={records[0].source_sha256}:actual={source_sha256}"
            )
        sidecar = {
            "schema_version": ACCOUNTING_AUDIT_SCHEMA_VERSION,
            "source": {
                "artifact_path": str(args.source),
                "sha256": source_sha256,
                "size_bytes": source_size_bytes,
            },
            "registry": {
                "path": str(args.registry),
                "schema_version": registry.schema_version,
            },
            "records": [record.to_dict() for record in records],
        }
        encoded = _canonical_json_bytes(sidecar)
        _write_atomically(args.output, encoded)
    except (AccountingValidityRegistryError, OSError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "source_sha256": source_sha256}, sort_keys=True))
    return 0


def _source_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")


def _write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
