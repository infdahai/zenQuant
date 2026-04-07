#!/usr/bin/env python3
# ruff: noqa: I001
"""Thin wrapper for exporting live/paper audit data."""

from zenquant.app.live_export import main


if __name__ == "__main__":
    raise SystemExit(main())
