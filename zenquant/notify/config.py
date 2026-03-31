"""Configuration for the Telegram notification service."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    token: str = ""
    chat_id: str = ""
    enabled: bool = False
    rate_limit_seconds: int = 60
    daily_summary_hour_utc: int = 0

    @classmethod
    def from_env(cls) -> TelegramConfig:
        enabled = os.getenv("ZENQUANT_TELEGRAM_ENABLED", "").lower() in {"1", "true", "yes"}
        return cls(
            token=os.getenv("ZENQUANT_TELEGRAM_TOKEN", ""),
            chat_id=os.getenv("ZENQUANT_TELEGRAM_CHAT_ID", ""),
            enabled=enabled,
            rate_limit_seconds=int(os.getenv("ZENQUANT_TELEGRAM_RATE_LIMIT", "60")),
            daily_summary_hour_utc=int(os.getenv("ZENQUANT_TELEGRAM_DAILY_HOUR", "0")),
        )
