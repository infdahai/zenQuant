"""Local sealed-source availability policy; portable raw fixtures stay mandatory."""

import pytest

from zenquant.research.options_platform import panel as adapter


def pytest_addoption(parser):
    parser.addoption(
        "--require-local-options-panel",
        action="store_true",
        help="Require the fixed pinned local options panel and scout; never skip missing sources.",
    )


def require_local_options_panel(*, strict):
    roots = (adapter._PANEL, adapter._SCOUT)
    absent = []
    for root in roots:
        # A broken symlink or incomplete directory is present but invalid.
        if not root.exists() and not root.is_symlink():
            absent.append(str(root))
        else:
            adapter._sealed(root)
    if absent:
        reason = "LOCAL_OPTIONS_PANEL_ABSENT: " + ", ".join(absent) + "; source verification NOT performed"
        if strict:
            pytest.fail(reason)
        pytest.skip(reason)
    return adapter._PANEL


@pytest.fixture
def local_options_panel_root(request):
    return require_local_options_panel(strict=request.config.getoption("--require-local-options-panel"))
