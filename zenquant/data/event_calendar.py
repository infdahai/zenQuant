"""Macro event calendar — scheduled volatility events.

FOMC and CPI releases cause 1-3% BTC moves concentrated in 0-4H window.
At 4H execution, directional alpha is zero (fully priced in).
But volatility expansion is predictable → reduce leverage during event bars.

Usage:
    from zenquant.data.event_calendar import is_macro_event_bar
    if is_macro_event_bar(cutoff_time_ns):
        lev_cap *= 0.75  # Reduce 25% during event
"""

from __future__ import annotations

from datetime import UTC, datetime

# FOMC meeting dates (announcement at 14:00 ET = 18:00 UTC)
# Source: federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES_2025 = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
]
FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-16",
]

# CPI release dates (08:30 ET = 12:30 UTC, typically 10th-13th of month)
CPI_DATES_2025 = [
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-11", "2025-08-12",
    "2025-09-10", "2025-10-14", "2025-11-12", "2025-12-10",
]
CPI_DATES_2026 = [
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-14",
    "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-10", "2026-10-13", "2026-11-12", "2026-12-10",
]

# Combine all event dates
ALL_EVENT_DATES: set[str] = set(
    FOMC_DATES_2025 + FOMC_DATES_2026 + CPI_DATES_2025 + CPI_DATES_2026
)


def is_macro_event_bar(cutoff_time_ns: int) -> bool:
    """Check if this 4H bar contains a scheduled macro event.

    Returns True for the 4H bar that contains the event release time.
    FOMC: 18:00 UTC → falls in 16:00-20:00 UTC bar
    CPI:  12:30 UTC → falls in 12:00-16:00 UTC bar
    """
    dt = datetime.fromtimestamp(cutoff_time_ns / 1e9, tz=UTC)
    date_str = dt.strftime("%Y-%m-%d")
    return date_str in ALL_EVENT_DATES


def get_event_type(cutoff_time_ns: int) -> str:
    """Return event type for this bar, or empty string."""
    dt = datetime.fromtimestamp(cutoff_time_ns / 1e9, tz=UTC)
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in set(FOMC_DATES_2025 + FOMC_DATES_2026):
        return "FOMC"
    if date_str in set(CPI_DATES_2025 + CPI_DATES_2026):
        return "CPI"
    return ""
