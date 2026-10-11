"""Shared test configuration for infra-gated tests."""

import os

# PostgreSQL test database (local)
if not os.environ.get("ZENQUANT_TEST_PG_DSN"):
    os.environ["ZENQUANT_TEST_PG_DSN"] = "postgresql://localhost/zenquant_test"


def pytest_addoption(parser):
    # Root discovery must register this before options/conftest.py is collected.
    parser.addoption(
        "--require-local-options-panel",
        action="store_true",
        help="Require the fixed pinned local options panel and scout; never skip missing sources.",
    )
