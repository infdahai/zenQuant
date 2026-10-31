"""Independent tests for the offline options panel exporter."""

import importlib.util
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/export_options_workbench_panel.py"


def exporter():
    spec = importlib.util.spec_from_file_location("rust_panel_exporter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_canonical_decimal_is_context_independent():
    module = exporter()
    results = []
    for precision in (6, 100):
        with localcontext() as context:
            context.prec = precision
            results.append(module.native_hash({"z": Decimal("123456789.1234567800"), "a": Decimal("-0")}))
    assert results[0] == results[1]
    assert module.native_payload(Decimal("1.2300")) == "1.23"
    with pytest.raises(ValueError):
        module.native_hash({"bad": 1.5})
    with pytest.raises(ValueError):
        module.native_payload(Decimal("0." + "1" * 29))


def test_exporter_no_replace_traversal_symlink_and_source_overlap(tmp_path, monkeypatch):
    module = exporter()
    root = tmp_path / "source"
    root.mkdir()
    output = tmp_path / "panel.json"
    output.write_text("existing output")
    before = output.read_bytes()
    monkeypatch.setattr(module, "TRUSTED_ROOTS", {root: "unused"})
    for target in (output, root / "forbidden.json", tmp_path / "unused" / ".." / "bad.json"):
        with pytest.raises((ValueError, OSError)):
            module.export_panel(root, target)
    link = tmp_path / "link"
    link.symlink_to(output.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        module.safe_path(link / "bad.json")
    assert output.read_bytes() == before
    assert not (tmp_path / "unused").exists()


def test_exporter_rejects_unbound_projection(tmp_path, monkeypatch):
    from dataclasses import replace

    from zenquant.research.options_platform.panel import load_panel

    from .test_panel import write_minimal_panel

    root = write_minimal_panel(tmp_path, monkeypatch)
    panel = load_panel(root)
    with pytest.raises(ValueError):
        exporter().project_panel(replace(panel, verification=None), [])
