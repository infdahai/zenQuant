#!/usr/bin/env python3
"""Freeze local research as inert evidence; never merge it into runtime paths.

Run snapshot first, inspect its manifest, then archive. Source is read-only.
Local settings are excluded before any content access or hashing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

EXCLUDED_PARTS = {".claude", ".git", ".venv", "node_modules", "data", "datasets", "results", "__pycache__"}
EXCLUDED_NAMES = {".env"}


def git(source: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args])


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def excluded(path: str) -> bool:
    p = Path(path)
    # zenquant/data is Python source, whereas root data/ contains datasets.
    blocked = EXCLUDED_PARTS - {"data"}
    return (
        bool(set(p.parts) & blocked) or p.parts[0] == "data" or p.name in EXCLUDED_NAMES or p.name.startswith(".env.")
    )


def check_content(data: bytes) -> None:
    if b"\0" in data:
        raise ValueError("binary payload is outside this text archive")
    data.decode("utf-8")
    if re.search(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}",
        data,
    ):
        raise ValueError("possible credential in archive payload")


def blob(source: Path, revision: str, path: str) -> bytes | None:
    exists = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(source), "cat-file", "-e", f"{revision}:{path}"], capture_output=True
    )
    return git(source, "show", f"{revision}:{path}") if exists.returncode == 0 else None


def inventory(source: Path) -> tuple[dict, dict[str, bytes]]:
    refs = {name: git(source, "rev-parse", name).decode().strip() for name in ("HEAD", "main", "personal/main")}
    if refs["HEAD"] != refs["main"]:
        raise ValueError("source must remain on the recorded local main")
    base = git(source, "merge-base", refs["main"], refs["personal/main"]).decode().strip()
    raw_status = git(source, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    dirty = {}
    for item in raw_status.split(b"\0"):
        if not item:
            continue
        text = item.decode()
        if text[:2].strip() in {"R", "C"} or "R" in text[:2]:
            raise ValueError("rename status requires explicit review")
        dirty[text[3:]] = text[:2]
    changed = git(source, "diff", "--name-only", "-z", base, refs["main"]).decode().split("\0")
    committed = set(filter(None, changed))
    files, payloads = [], {}
    for path in sorted(committed | dirty.keys()):
        row = {"path": path, "dirty_status": dirty.get(path), "local_committed_change": path in committed}
        if excluded(path):
            row.update(
                disposition="excluded",
                reason="local settings, secrets, datasets, results or dependency directories; content not read",
            )
            files.append(row)
            continue
        p = source / path
        if p.is_symlink():
            raise ValueError(f"symlink requires explicit review: {path}")
        versions = {}
        for label, revision in (("base", base), ("personal_main", refs["personal/main"]), ("local_main", refs["main"])):
            data = blob(source, revision, path)
            versions[label] = None if data is None else digest(data)
            if label == "local_main" and path in committed and data is not None:
                check_content(data)
                payloads[f"committed/{path}.txt"] = data
        if path in dirty:
            data = p.read_bytes() if p.exists() else None
            versions["working"] = None if data is None else digest(data)
            if data is not None:
                check_content(data)
                payloads[f"working/{path}.txt"] = data
        row.update(
            sha256=versions,
            disposition="archive_only",
            reason="preserve local research without restoring retired Python authority or replacing current policy",
            payloads=[key for key in payloads if key in (f"committed/{path}.txt", f"working/{path}.txt")],
        )
        files.append(row)
    commits = []
    for sha in git(source, "rev-list", "--reverse", f"{refs['personal/main']}..{refs['main']}").decode().splitlines():
        meta = git(source, "show", "-s", "--format=%H%n%P%n%s", sha).decode().splitlines()
        # First-parent changes also retain the local consolidation merge; every
        # ancestor unique to local main receives its own record and patch.
        paths = list(
            filter(
                None,
                git(
                    source,
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-m",
                    "--first-parent",
                    "-z",
                    sha,
                )
                .decode()
                .split("\0"),
            )
        )
        allowed = [p for p in paths if not excluded(p)]
        patch_path = f"history/{sha}.patch.txt"
        patch = (
            git(
                source,
                "show",
                "--format=fuller",
                "--first-parent",
                "--diff-merges=first-parent",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                sha,
                "--",
                *allowed,
            )
            if allowed
            else b""
        )
        check_content(patch)
        payloads[patch_path] = patch
        commits.append(
            {
                "sha": sha,
                "parents": meta[1].split(),
                "subject": meta[2],
                "paths": paths,
                "excluded_paths": [p for p in paths if excluded(p)],
                "patch": patch_path,
            }
        )
    manifest = {
        "schema": "zenquant.local-research-consolidation.v1",
        "source": str(source.resolve()),
        "refs": refs,
        "merge_base": base,
        "status_sha256": digest(raw_status),
        "files": files,
        "commits": commits,
        "payloads": {key: {"sha256": digest(value), "bytes": len(value)} for key, value in sorted(payloads.items())},
    }
    return manifest, payloads


def snapshot(source: Path, output: Path) -> None:
    manifest, _ = inventory(source)
    output.write_text(json.dumps(manifest, indent=2) + "\n")


def archive(source: Path, snapshot_path: Path, output: Path) -> None:
    expected = json.loads(snapshot_path.read_text())
    current, payloads = inventory(source)
    if expected != current:
        raise ValueError("source changed since snapshot; refusing copy")
    if output.exists():
        raise ValueError("archive destination already exists")
    # Recheck all hashes and Git state before any destination write.
    final, _ = inventory(source)
    if final != expected:
        raise ValueError("source changed during read; refusing copy")
    output.mkdir(parents=True)
    for path, data in payloads.items():
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (output / "manifest.json").write_text(json.dumps(current, indent=2) + "\n")
    verify(output)


def verify(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text())
    expected = set(manifest["payloads"]) | {"manifest.json"}
    actual = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("archive has missing or unexpected files")
    for path, metadata in manifest["payloads"].items():
        data = (output / path).read_bytes()
        if digest(data) != metadata["sha256"] or len(data) != metadata["bytes"]:
            raise ValueError(f"archive hash mismatch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "archive", "verify"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "verify":
        verify(args.output)
    elif args.action == "snapshot":
        snapshot(args.source, args.output)
    else:
        archive(args.source, args.snapshot, args.output)


if __name__ == "__main__":
    main()
