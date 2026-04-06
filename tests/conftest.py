"""Shared test configuration — auto-sets environment variables for infra-gated tests."""

import os

# PostgreSQL test database (local)
if not os.environ.get("ZENQUANT_TEST_PG_DSN"):
    os.environ["ZENQUANT_TEST_PG_DSN"] = "postgresql://localhost/zenquant_test"

# Deribit public API (no auth needed)
if not os.environ.get("ZENQUANT_RUN_DERIBIT_NETWORK_TESTS"):
    os.environ["ZENQUANT_RUN_DERIBIT_NETWORK_TESTS"] = "1"
