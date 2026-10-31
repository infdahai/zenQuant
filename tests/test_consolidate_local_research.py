"""Read-only consolidation contracts; no datasets or project runtime required."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "consolidation", Path(__file__).resolve().parents[1] / "scripts/consolidate_local_research.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.source / "base.py").write_text("value = 1\n")
        self.git("add", "base.py")
        self.git("commit", "-m", "base")
        self.git("branch", "personal/main")
        (self.source / "base.py").write_text("value = 2\n")
        self.git("commit", "-am", "local")
        (self.source / "base.py").write_text("value = 3\n")
        (self.source / "new.py").write_text("new = True\n")
        self.snapshot = self.root / "snapshot.json"
        self.output = self.root / "archive"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], stderr=subprocess.DEVNULL)

    def test_preserves_committed_dirty_untracked_and_history(self):
        module.snapshot(self.source, self.snapshot)
        module.archive(self.source, self.snapshot, self.output)
        module.verify(self.output)
        self.assertEqual((self.output / "committed/base.py.txt").read_text(), "value = 2\n")
        self.assertEqual((self.output / "working/base.py.txt").read_text(), "value = 3\n")
        self.assertEqual((self.output / "working/new.py.txt").read_text(), "new = True\n")
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(len(manifest["commits"]), 1)
        self.assertEqual((self.source / "base.py").read_text(), "value = 3\n")

    def test_source_change_refuses_copy(self):
        module.snapshot(self.source, self.snapshot)
        (self.source / "new.py").write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "source changed"):
            module.archive(self.source, self.snapshot, self.output)
        self.assertFalse(self.output.exists())

    def test_settings_never_read_even_if_symlink(self):
        (self.source / ".claude").mkdir()
        (self.source / ".claude/settings.local.json").symlink_to("/unreadable-secret")
        manifest, payloads = module.inventory(self.source)
        row = next(row for row in manifest["files"] if row["path"].startswith(".claude/"))
        self.assertEqual(row["disposition"], "excluded")
        self.assertNotIn("sha256", row)
        self.assertFalse(any(".claude/" in path for path in payloads))

    def test_tamper_and_extra_payload_rejected(self):
        module.snapshot(self.source, self.snapshot)
        module.archive(self.source, self.snapshot, self.output)
        (self.output / "working/new.py.txt").write_text("tamper")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            module.verify(self.output)
        (self.output / "extra").write_text("extra")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            module.verify(self.output)

    def test_deleted_local_file_recorded(self):
        (self.source / "base.py").unlink()
        manifest, payloads = module.inventory(self.source)
        row = next(row for row in manifest["files"] if row["path"] == "base.py")
        self.assertIsNone(row["sha256"]["working"])
        self.assertIn("committed/base.py.txt", payloads)

    def test_source_data_modules_are_not_datasets(self):
        self.assertFalse(module.excluded("zenquant/data/spot_daily.py"))
        self.assertTrue(module.excluded("data/BTC.csv"))
        self.assertTrue(module.excluded("results/report.json"))
        self.assertTrue(module.excluded("node_modules/example/index.js"))
        self.assertTrue(module.excluded(".venv/lib/example.py"))

    def test_lockfiles_preserved_as_inert_source(self):
        for path in ("uv.lock", "rust/Cargo.lock", "package-lock.json"):
            self.assertFalse(module.excluded(path))
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("version = 1\n")
        self.git("add", "uv.lock", "rust/Cargo.lock", "package-lock.json")
        self.git("commit", "-m", "lockfiles")
        (self.source / "uv.lock").write_text("version = 2\n")
        module.snapshot(self.source, self.snapshot)
        module.archive(self.source, self.snapshot, self.output)
        module.verify(self.output)
        self.assertEqual((self.output / "working/uv.lock.txt").read_text(), "version = 2\n")
        for path in ("uv.lock", "rust/Cargo.lock", "package-lock.json"):
            self.assertEqual((self.output / f"committed/{path}.txt").read_text(), "version = 1\n")
        manifest = json.loads((self.output / "manifest.json").read_text())
        commit = manifest["commits"][-1]
        self.assertEqual(commit["excluded_paths"], [])
        patch = (self.output / commit["patch"]).read_text()
        self.assertIn("rust/Cargo.lock", patch)

    def test_secret_and_binary_payload_rejected(self):
        for data in (b"-----BEGIN PRIVATE KEY-----", b"binary\0data"):
            with self.assertRaises(ValueError):
                module.check_content(data)


if __name__ == "__main__":
    unittest.main()
