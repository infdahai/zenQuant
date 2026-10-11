"""Offline, no-replace transport of the sealed P1 panel into Rust v1.

Raw seals verify bytes, never the exported projection or trading permission.
Run with the original repository on PYTHONPATH; the P1 loader owns its roots.
"""

import argparse
import json
import os
import secrets
import stat
import sys
from dataclasses import fields, is_dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from zenquant.options.contracts import fingerprint
from zenquant.research.options_platform.panel import input_integrity, load_panel

METHOD = "exact_decimal.rust.v1"
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
REPOSITORY = Path(sys.modules[load_panel.__module__].__file__).resolve().parents[3]
TRUSTED_ROOTS = {
    REPOSITORY
    / "results/crypto_opportunity/20261009_options_liquidity_panel_v1": "b61d04c898cd53545e2bc95ffdede6da6861b18d602d49cb83d57eb52816423b",
    REPOSITORY
    / "results/crypto_opportunity/20261009_options_venue_scout_v1": "c35edb8ad23c8d958fe8f2383786f963160842c55ea8c3489e36efb96d25b983",
}


def native_payload(value):
    """Canonical finite decimals without ambient Decimal-context rounding."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("INVALID_INPUT")
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if value == 0:
            return "0"
        digits = text.lstrip("-").replace(".", "").lstrip("0")
        fractional = len(text.split(".")[1]) if "." in text else 0
        if len(digits) > 64 or fractional > 28:
            raise ValueError("INVALID_INPUT")
        return text
    if is_dataclass(value):
        return {field.name: native_payload(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("INVALID_INPUT")
        return {key: native_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [native_payload(item) for item in value]
    if value is None or type(value) in (int, bool, str):
        return value
    raise ValueError("INVALID_INPUT")


def canonical_bytes(value):
    return json.dumps(
        native_payload(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def native_hash(value):
    return sha256(METHOD.encode() + b"\0" + canonical_bytes(value)).hexdigest()


def safe_path(path):
    """Reject original traversal and every symlink before lexical normalization."""
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("INVALID_INPUT")
    path = Path.cwd() / path if not path.is_absolute() else path
    for component in [*reversed(path.parents), path]:
        if component.is_symlink():
            raise ValueError("INVALID_INPUT")
    return path


def open_directory(path):
    path = safe_path(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_bounded(path):
    path = safe_path(path)
    parent = open_directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                raise ValueError("INVALID_INPUT")
            data = stream.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ValueError("INVALID_INPUT")
            return data
    finally:
        os.close(parent)


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("INVALID_INPUT")
            result[key] = value
        return result

    def reject(_):
        raise ValueError("INVALID_INPUT")

    return json.loads(data, object_pairs_hook=unique, parse_float=reject, parse_constant=reject)


def source_pins():
    pins, total = [], 0
    for root, digest in TRUSTED_ROOTS.items():
        seal = root / "seal.json"
        raw = read_bounded(seal)
        if sha256(raw).hexdigest() != digest:
            raise ValueError("INVALID_INPUT")
        entries = [(seal, {"bytes": len(raw), "sha256": digest})]
        for name, info in strict_json(raw)["files"].items():
            relative = Path(name)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in ("..", ".") for part in relative.parts)
                or name != relative.as_posix()
                or name == "seal.json"
            ):
                raise ValueError("INVALID_INPUT")
            entries.append((root / relative, info))
        for path, info in entries:
            if len(pins) >= 1024:
                raise ValueError("INVALID_INPUT")
            data = read_bounded(path)
            total += len(data)
            if (
                total > MAX_TOTAL
                or type(info["bytes"]) is not int
                or len(data) != info["bytes"]
                or sha256(data).hexdigest() != info["sha256"]
            ):
                raise ValueError("INVALID_INPUT")
            pins.append({"path": str(path), "size_bytes": len(data), "sha256": sha256(data).hexdigest()})
    if len({pin["path"] for pin in pins}) != len(pins):
        raise ValueError("INVALID_INPUT")
    return sorted(pins, key=lambda pin: pin["path"])


def project_panel(panel, pins):
    if input_integrity(panel) != "PINNED_BYTES_VERIFIED":
        raise ValueError("INVALID_INPUT")
    protocol = strict_json(panel.protocol_json)
    quotes = []
    for record in panel.records:
        book = native_payload(record.book)
        original = record.book.spec
        book["spec"].update(provenance="sealed_proxy", exercise_style="UNKNOWN", settlement_rule="UNKNOWN")
        assumptions = list(panel.assumptions) + [
            "EXPORTED_PROJECTION_UNATTESTED",
            "original_rule_claim=" + original.settlement_rule,
            "original_exercise_claim=" + original.exercise_style,
            "original_index_observation=" + canonical_bytes(record.index).decode(),
            "index quote denomination not independently proven; no USD/stablecoin conversion supplied",
            "missing fee knowledge time and terminal fee; preregistration fee/cap proxies only",
            "metadata_reference=" + record.metadata_reference,
            f"round={record.round};cell_id={record.cell_id};family={record.family}",
        ]
        assumptions.extend(
            "selection_observation=" + canonical_bytes(observation).decode()
            for observation in panel.observations
            if observation.source_id.startswith("Bybit:") and f":{original.instrument_id}:" in observation.source_id
        )
        quotes.append(
            {
                "quote_id": f"{record.round}:{record.cell_id}:{record.family}",
                "book": book,
                "index_in_quote": None,
                "index_currency": None,
                "quote_to_report": None,
                "fx_pair": None,
                "fee_policy": {
                    "rate": native_payload(Decimal(protocol["taker_fee_rate_proxy"])),
                    "premium_cap": native_payload(Decimal(protocol["premium_fee_caps"][original.venue])),
                    "fee_currency": original.quote_currency,
                    "provenance": "sealed_proxy",
                },
                "fee_known_ns": None,
                "metadata_known_ns": original.valid_from_ns,
                "timing": {
                    "max_age_ns": protocol["maximum_pair_quote_age_at_receipt_ms"] * 10**6,
                    "minimum_source_age_ns": protocol["minimum_allowed_quote_age_at_receipt_ms"] * 10**6,
                },
                "source_identities": list(panel.identities)
                + [
                    ("p1_spec_fingerprint", fingerprint(original)),
                    ("p1_book_fingerprint", fingerprint(record.book)),
                    ("p1_panel_fingerprint", panel.verification.canonical_input_hash),
                    ("raw_request", record.request_hash),
                ],
                "assumptions": assumptions,
            }
        )
    if len(quotes) > 512:
        raise ValueError("INVALID_INPUT")
    gaps = [{"status": gap.status, "reason": f"{gap.reason}:{gap.identity}"} for gap in panel.gaps]
    gaps.append({"status": "NOT_EVALUABLE", "reason": "INDEX_DENOMINATION_FX_FEE_TIME_TERMINAL_FEE_UNVERIFIED"})
    return {
        "schema_version": "options-workbench-panel.rust.v1",
        "exporter_version": "options-workbench-exporter.rust.v1",
        "raw_source_integrity": "RAW_PINNED_BYTES_VERIFIED",
        "projection_trust": "EXPORTED_PROJECTION_UNATTESTED",
        "pinned_sources": pins,
        "projection_sha256": native_hash(quotes),
        "quotes": quotes,
        "gaps": gaps,
    }


def export_panel(root, output):
    root, output = safe_path(root), safe_path(output)
    if root not in TRUSTED_ROOTS or output.exists():
        raise ValueError("INVALID_INPUT")
    if any(output == source or output in source.parents or source in output.parents for source in TRUSTED_ROOTS):
        raise ValueError("INVALID_INPUT")
    parent = open_directory(output.parent)  # Existing parent only; no invalid-input mkdir.
    stage = None
    try:
        pins = source_pins()  # Bound all reads before invoking the public P1 loader.
        payload = canonical_bytes(project_panel(load_panel(root), pins)) + b"\n"
        if len(payload) > MAX_FILE:
            raise ValueError("INVALID_INPUT")
        # Re-read all raw pins after projection; concurrent source mutation fails closed.
        if source_pins() != pins:
            raise ValueError("INVALID_INPUT")
        stage = ".options-panel-incomplete-" + secrets.token_hex(16)
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        check = open_directory(output.parent)
        try:
            if (os.fstat(check).st_dev, os.fstat(check).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                raise ValueError("INVALID_INPUT")
        finally:
            os.close(check)
        os.link(stage, output.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink(stage, dir_fd=parent)
        stage = None
        os.fsync(parent)
    finally:
        if stage is not None:
            os.unlink(stage, dir_fd=parent)
        os.close(parent)


class SafeParser(argparse.ArgumentParser):
    def error(self, _):
        self.exit(2, "INVALID_INPUT\n")


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--panel-root", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        export_panel(args.panel_root, args.output_file)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        print("INVALID_INPUT", file=sys.stderr)
        return 2
    print("EXPORTED_PROJECTION_UNATTESTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
