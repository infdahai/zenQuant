"""Build one verifiable GitHub Actions registration envelope."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zenquant.research.registration import (
    RegistrationError,
    build_registration_envelope,
    verify_github_receipt,
)


def _load_provider_run(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"cannot load provider run payload: {path}") from exc
    if not isinstance(payload, dict):
        raise RegistrationError("provider run payload must be a JSON object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--provider-run-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    provider_run = _load_provider_run(args.provider_run_json)
    envelope = build_registration_envelope(args.manifest_path, args.commit_sha, provider_run)
    verify_github_receipt(envelope, provider_run)
    args.output.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
