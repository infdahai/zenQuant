"""Exercise the local panel policy through root and targeted pytest CLI discovery."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("discovery", [(), ("tests/options",)], ids=["root", "options"])
@pytest.mark.parametrize("strict", [False, True], ids=["portable", "strict"])
def test_local_panel_option_is_registered_before_cli_parsing(tmp_path, discovery, strict):
    # Moving registration into the options subtree breaks root discovery. A
    # getoption default would also hide portable failure but still reject the flag.
    source_tests = Path(__file__).parent
    options = tmp_path / "tests" / "options"
    options.mkdir(parents=True)
    shutil.copy2(source_tests / "conftest.py", options.parent / "conftest.py")
    shutil.copy2(source_tests / "options" / "conftest.py", options / "conftest.py")
    (options / "test_probe.py").write_text(
        """import pytest
from zenquant.research.options_platform import panel as adapter

@pytest.fixture(autouse=True)
def absent_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_PANEL", tmp_path / "absent_panel")
    monkeypatch.setattr(adapter, "_SCOUT", tmp_path / "absent_scout")

def test_probe(local_options_panel_root):
    pytest.fail("Absent local sources must never reach the test body")
"""
    )
    command = [sys.executable, "-m", "pytest", "-q", "-rs", *discovery]
    if strict:
        command.append("--require-local-options-panel")
    env = dict(os.environ, PYTHONPATH=str(source_tests.parent), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == (1 if strict else 0), output
    assert "LOCAL_OPTIONS_PANEL_ABSENT" in output, output
    assert ("1 error" if strict else "1 skipped") in output, output
