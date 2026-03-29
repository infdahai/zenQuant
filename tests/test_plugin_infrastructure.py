"""Tests for plugin infrastructure: protocols, registry, orchestrator, profile."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from zenquant.plugins.orchestrator import (
    compose_exits,
    compose_filters,
    compose_signals,
    compose_sizers,
)
from zenquant.plugins.protocols import (
    ExitResult,
    FilterResult,
    PluginMeta,
    SignalResult,
    SizerResult,
)
from zenquant.plugins.registry import LoadedPlugin, PluginRegistry, load_registry


# ---------------------------------------------------------------------------
# Helpers: build LoadedPlugin from lambdas for unit testing
# ---------------------------------------------------------------------------


def _signal_plugin(name: str, value: float, confidence: float = 0.8, weight: float = 1.0) -> LoadedPlugin:
    return LoadedPlugin(
        meta=PluginMeta(name=name, version="1.0", plugin_type="signal", description=name),
        compute=lambda ind, st, cfg: SignalResult(
            value=value, confidence=confidence, reason_codes=[f"{name.upper()}"]
        ),
        config={},
        weight=weight,
        source_path=f"<test>/{name}.py",
    )


def _filter_plugin(name: str, passed: bool) -> LoadedPlugin:
    return LoadedPlugin(
        meta=PluginMeta(name=name, version="1.0", plugin_type="filter", description=name),
        compute=lambda ind, st, cfg: FilterResult(
            passed=passed, reason="" if passed else f"{name} blocked", reason_codes=[f"{name.upper()}"]
        ),
        config={},
        weight=1.0,
        source_path=f"<test>/{name}.py",
    )


def _sizer_plugin(name: str, factor: float) -> LoadedPlugin:
    return LoadedPlugin(
        meta=PluginMeta(name=name, version="1.0", plugin_type="sizer", description=name),
        compute=lambda ind, st, cfg: SizerResult(
            factor=factor, reason_codes=[f"{name.upper()}"]
        ),
        config={},
        weight=1.0,
        source_path=f"<test>/{name}.py",
    )


def _exit_plugin(name: str, should_exit: bool, priority: int = 99) -> LoadedPlugin:
    return LoadedPlugin(
        meta=PluginMeta(name=name, version="1.0", plugin_type="exit", description=name),
        compute=lambda ind, st, pos, cfg: ExitResult(
            should_exit=should_exit,
            exit_type=name.upper() if should_exit else "NONE",
            urgency="IMMEDIATE" if should_exit else "NONE",
            reduction_pct=1.0 if should_exit else 0.0,
            reason_codes=[f"{name.upper()}"] if should_exit else [],
        ),
        config={},
        weight=float(priority),
        source_path=f"<test>/{name}.py",
    )


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------


class TestProtocols(unittest.TestCase):
    """Verify result types are frozen and well-formed."""

    def test_signal_result_frozen(self):
        r = SignalResult(value=0.5, confidence=0.8, reason_codes=["TEST"])
        with self.assertRaises(AttributeError):
            r.value = 1.0  # type: ignore[misc]

    def test_filter_result_defaults(self):
        r = FilterResult(passed=True)
        self.assertEqual(r.reason, "")
        self.assertEqual(r.reason_codes, [])

    def test_exit_result_defaults(self):
        r = ExitResult(should_exit=False)
        self.assertEqual(r.exit_type, "NONE")
        self.assertIsNone(r.stop_price)


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


class TestComposeSignals(unittest.TestCase):

    def test_empty_plugins(self):
        result = compose_signals([], {}, {}, {})
        self.assertEqual(result.direction, 0.0)
        self.assertEqual(result.confidence, 0.0)

    def test_single_signal(self):
        plugins = [_signal_plugin("mom", value=0.8, confidence=0.9)]
        weights = {"mom": 1.0}
        result = compose_signals(plugins, weights, {}, {})
        self.assertAlmostEqual(result.direction, 0.8, places=4)
        self.assertAlmostEqual(result.confidence, 0.9, places=4)

    def test_weighted_combination(self):
        plugins = [
            _signal_plugin("bull", value=1.0, confidence=0.9),
            _signal_plugin("bear", value=-1.0, confidence=0.7),
        ]
        weights = {"bull": 0.7, "bear": 0.3}
        result = compose_signals(plugins, weights, {}, {})
        # expected: 1.0 * 0.7 + (-1.0) * 0.3 = 0.4
        self.assertAlmostEqual(result.direction, 0.4, places=4)
        self.assertIn("bull", result.contributions)
        self.assertIn("bear", result.contributions)

    def test_direction_clamped(self):
        plugins = [
            _signal_plugin("a", value=1.0),
            _signal_plugin("b", value=1.0),
        ]
        weights = {"a": 1.0, "b": 1.0}
        result = compose_signals(plugins, weights, {}, {})
        self.assertLessEqual(result.direction, 1.0)

    def test_reason_codes_namespaced(self):
        plugins = [_signal_plugin("mom", value=0.5)]
        result = compose_signals(plugins, {"mom": 1.0}, {}, {})
        self.assertTrue(any(c.startswith("mom:") for c in result.reason_codes))


class TestComposeFilters(unittest.TestCase):

    def test_all_pass(self):
        plugins = [_filter_plugin("a", True), _filter_plugin("b", True)]
        result = compose_filters(plugins, {}, {})
        self.assertTrue(result.passed)
        self.assertEqual(result.blockers, [])

    def test_one_blocks(self):
        plugins = [_filter_plugin("a", True), _filter_plugin("b", False)]
        result = compose_filters(plugins, {}, {})
        self.assertFalse(result.passed)
        self.assertEqual(result.blockers, ["b"])

    def test_empty_passes(self):
        result = compose_filters([], {}, {})
        self.assertTrue(result.passed)


class TestComposeSizers(unittest.TestCase):

    def test_multiplicative(self):
        plugins = [_sizer_plugin("a", 0.8), _sizer_plugin("b", 0.5)]
        result = compose_sizers(plugins, {}, {})
        self.assertAlmostEqual(result.factor, 0.4, places=4)

    def test_empty_is_identity(self):
        result = compose_sizers([], {}, {})
        self.assertAlmostEqual(result.factor, 1.0, places=4)

    def test_floor_at_zero(self):
        plugins = [_sizer_plugin("a", -0.5)]
        result = compose_sizers(plugins, {}, {})
        self.assertEqual(result.factor, 0.0)

    def test_breakdown_recorded(self):
        plugins = [_sizer_plugin("vol", 0.9), _sizer_plugin("conf", 0.7)]
        result = compose_sizers(plugins, {}, {})
        self.assertIn("vol", result.breakdown)
        self.assertIn("conf", result.breakdown)


class TestComposeExits(unittest.TestCase):

    def test_first_match_wins(self):
        plugins = [
            _exit_plugin("inval", should_exit=True, priority=1),
            _exit_plugin("trail", should_exit=True, priority=2),
        ]
        result = compose_exits(plugins, {}, {}, {})
        self.assertIsNotNone(result)
        self.assertEqual(result.exit_type, "INVAL")

    def test_no_exit(self):
        plugins = [
            _exit_plugin("a", should_exit=False, priority=1),
            _exit_plugin("b", should_exit=False, priority=2),
        ]
        result = compose_exits(plugins, {}, {}, {})
        self.assertIsNone(result)

    def test_empty_no_exit(self):
        result = compose_exits([], {}, {}, {})
        self.assertIsNone(result)

    def test_priority_order_respected(self):
        # Lower priority number = checked first
        plugins = [
            _exit_plugin("low_pri", should_exit=True, priority=10),
            _exit_plugin("high_pri", should_exit=True, priority=1),
        ]
        # Pre-sort by priority (registry does this)
        plugins.sort(key=lambda p: p.weight)
        result = compose_exits(plugins, {}, {}, {})
        self.assertEqual(result.exit_type, "HIGH_PRI")


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestPluginRegistry(unittest.TestCase):

    def test_summary(self):
        registry = PluginRegistry(
            signals=[_signal_plugin("a", 0.5)],
            filters=[_filter_plugin("b", True)],
        )
        registry._index = {"a": registry.signals[0], "b": registry.filters[0]}
        summary = registry.summary()
        self.assertEqual(summary["signals"], ["a"])
        self.assertEqual(summary["filters"], ["b"])

    def test_signal_weights(self):
        registry = PluginRegistry(
            signals=[
                _signal_plugin("a", 0.5, weight=0.7),
                _signal_plugin("b", 0.3, weight=0.3),
            ],
        )
        weights = registry.signal_weights
        self.assertAlmostEqual(weights["a"], 0.7)
        self.assertAlmostEqual(weights["b"], 0.3)


class TestLoadRegistry(unittest.TestCase):
    """Test loading plugins from the filesystem."""

    def _write_plugin(self, tmpdir: Path, plugin_type_dir: str, name: str, source: str) -> None:
        plugin_dir = tmpdir / plugin_type_dir
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "__init__.py").touch()
        (plugin_dir / f"{name}.py").write_text(textwrap.dedent(source))

    def test_load_signal_plugin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._write_plugin(tmp, "signals", "test_sig", """
                from zenquant.plugins.protocols import PluginMeta, SignalResult

                meta = PluginMeta(
                    name="test_sig",
                    version="1.0",
                    plugin_type="signal",
                    description="test",
                )

                def compute(indicators, state, config):
                    return SignalResult(value=0.42, confidence=0.9, reason_codes=["TEST"])
            """)

            profile = {"signals": {"test_sig": {"enabled": True, "weight": 0.6}}}
            registry = load_registry(profile, plugin_root=tmp)

            self.assertEqual(len(registry.signals), 1)
            self.assertEqual(registry.signals[0].meta.name, "test_sig")
            self.assertAlmostEqual(registry.signals[0].weight, 0.6)

            result = registry.signals[0].compute({}, {}, {})
            self.assertAlmostEqual(result.value, 0.42)

    def test_disabled_plugin_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._write_plugin(tmp, "signals", "disabled_sig", """
                from zenquant.plugins.protocols import PluginMeta, SignalResult
                meta = PluginMeta(name="disabled_sig", version="1.0", plugin_type="signal", description="test")
                def compute(indicators, state, config):
                    return SignalResult(value=0.0, confidence=0.0)
            """)

            profile = {"signals": {"disabled_sig": {"enabled": False}}}
            registry = load_registry(profile, plugin_root=tmp)
            self.assertEqual(len(registry.signals), 0)

    def test_config_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._write_plugin(tmp, "signals", "cfg_sig", """
                from dataclasses import dataclass
                from zenquant.plugins.protocols import PluginMeta, SignalResult

                @dataclass(frozen=True, slots=True)
                class Cfg:
                    threshold: float = 0.5
                    lookback: int = 20

                meta = PluginMeta(
                    name="cfg_sig", version="1.0", plugin_type="signal",
                    description="test", config_schema=Cfg,
                )

                def compute(indicators, state, config):
                    return SignalResult(
                        value=config.get("threshold", 0.0),
                        confidence=0.8,
                    )
            """)

            # Override threshold but not lookback
            profile = {"signals": {"cfg_sig": {"enabled": True, "weight": 1.0, "threshold": 0.8}}}
            registry = load_registry(profile, plugin_root=tmp)

            plugin = registry.signals[0]
            self.assertAlmostEqual(plugin.config["threshold"], 0.8)  # overridden
            self.assertEqual(plugin.config["lookback"], 20)  # default preserved

    def test_missing_plugin_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "signals").mkdir(parents=True)
            (tmp / "signals" / "__init__.py").touch()

            profile = {"signals": {"nonexistent": {"enabled": True}}}
            with self.assertRaises(FileNotFoundError):
                load_registry(profile, plugin_root=tmp)

    def test_wrong_type_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            self._write_plugin(tmp, "signals", "wrong_type", """
                from zenquant.plugins.protocols import PluginMeta, SignalResult
                meta = PluginMeta(name="wrong_type", version="1.0", plugin_type="filter", description="oops")
                def compute(indicators, state, config):
                    return SignalResult(value=0.0, confidence=0.0)
            """)

            profile = {"signals": {"wrong_type": {"enabled": True}}}
            with self.assertRaises(ValueError):
                load_registry(profile, plugin_root=tmp)

    def test_exit_sorted_by_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for name, prio in [("exit_a", 3), ("exit_b", 1), ("exit_c", 2)]:
                self._write_plugin(tmp, "exits", name, f"""
                    from zenquant.plugins.protocols import PluginMeta, ExitResult
                    meta = PluginMeta(name="{name}", version="1.0", plugin_type="exit", description="test")
                    def compute(indicators, state, position, config):
                        return ExitResult(should_exit=False)
                """)

            profile = {
                "exits": {
                    "exit_a": {"enabled": True, "priority": 3},
                    "exit_b": {"enabled": True, "priority": 1},
                    "exit_c": {"enabled": True, "priority": 2},
                }
            }
            registry = load_registry(profile, plugin_root=tmp)
            names = [p.meta.name for p in registry.exits]
            self.assertEqual(names, ["exit_b", "exit_c", "exit_a"])


# ---------------------------------------------------------------------------
# Profile loader tests
# ---------------------------------------------------------------------------


class TestProfileLoader(unittest.TestCase):

    def test_load_toml_profile(self):
        from zenquant.plugins.profile import load_profile

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".toml", delete=False) as f:
            f.write(b"""
[profile]
name = "test"

[signals.test_signal]
enabled = true
weight = 0.5
""")
            f.flush()
            profile = load_profile(Path(f.name))

        self.assertEqual(profile["profile"]["name"], "test")
        self.assertTrue(profile["signals"]["test_signal"]["enabled"])
        self.assertAlmostEqual(profile["signals"]["test_signal"]["weight"], 0.5)


if __name__ == "__main__":
    unittest.main()
