"""Shared test configuration for infra-gated tests."""

import os

# PostgreSQL test database (local)
if not os.environ.get("ZENQUANT_TEST_PG_DSN"):
    os.environ["ZENQUANT_TEST_PG_DSN"] = "postgresql://localhost/zenquant_test"
