"""Telegram notification service for ZenQuant.

Subscribes to the event bus for important trading events and sends
alerts via Telegram. Also provides bot commands for remote monitoring.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from zenquant.notify.config import TelegramConfig
from zenquant.notify.formatter import (
    format_daily_summary,
    format_execution,
    format_pnl_detail,
    format_position_change,
    format_position_detail,
    format_regime_change,
    format_risk_alert,
    format_risk_detail,
    format_status,
)

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trading alerts and responds to bot commands via Telegram.

    Lifecycle: instantiate → wire_bus() → start() → stop().
    """

    def __init__(self, config: TelegramConfig, snapshot_fn: Any = None) -> None:
        self._config = config
        self._snapshot_fn = snapshot_fn  # callable returning current dashboard snapshot
        self._last_sent: dict[str, float] = {}  # event_type → last_sent_ts
        self._last_regime: str = ""
        self._last_position_direction: int = 0
        self._app: Any = None
        self._task: asyncio.Task[None] | None = None
        self._daily_task: asyncio.Task[None] | None = None

    def wire_bus(self, bus: Any) -> None:
        """Subscribe to event bus topics for alert-worthy events."""
        bus.subscribe("state.market.>", self._on_market_state)
        bus.subscribe("strategy.position_state.>", self._on_position_state)
        bus.subscribe("risk.overlay.decision", self._on_risk_decision)
        bus.subscribe("exec.order.report", self._on_execution_report)

    async def start(self) -> None:
        """Start the Telegram bot polling loop."""
        if not self._config.enabled or not self._config.token:
            logger.info("Telegram notifier disabled (no token or not enabled)")
            return

        try:
            from telegram import Update
            from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
        except ImportError as err:
            raise ImportError(
                "python-telegram-bot is required when Telegram notifier is enabled. "
                "Install with: uv add python-telegram-bot"
            ) from err

        async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            snap = self._get_snapshot()
            text = format_status(snap)
            if update.effective_message:
                await update.effective_message.reply_text(text, parse_mode="Markdown")

        async def cmd_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            snap = self._get_snapshot()
            text = format_position_detail(snap)
            if update.effective_message:
                await update.effective_message.reply_text(text, parse_mode="Markdown")

        async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            snap = self._get_snapshot()
            text = format_risk_detail(snap)
            if update.effective_message:
                await update.effective_message.reply_text(text, parse_mode="Markdown")

        async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            snap = self._get_snapshot()
            text = format_pnl_detail(snap)
            if update.effective_message:
                await update.effective_message.reply_text(text, parse_mode="Markdown")

        self._app = ApplicationBuilder().token(self._config.token).build()
        self._app.add_handler(CommandHandler("status", cmd_status))
        self._app.add_handler(CommandHandler("position", cmd_position))
        self._app.add_handler(CommandHandler("risk", cmd_risk))
        self._app.add_handler(CommandHandler("pnl", cmd_pnl))

        await self._app.initialize()
        await self._app.start()
        self._task = asyncio.create_task(self._app.updater.start_polling(drop_pending_updates=True))
        self._daily_task = asyncio.create_task(self._daily_summary_loop())
        logger.info("Telegram notifier started")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._daily_task and not self._daily_task.done():
            self._daily_task.cancel()
        if self._app:
            try:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                logger.exception("Error stopping Telegram bot")
        logger.info("Telegram notifier stopped")

    # ── Event handlers (called synchronously from bus) ──

    def _on_market_state(self, _subject: str, payload: Any) -> None:
        regime = getattr(payload, "regime", None) or ""
        if not regime or regime == self._last_regime:
            return
        if not self._should_send("regime_change"):
            return
        old = self._last_regime or "UNKNOWN"
        self._last_regime = regime
        confidence = float(getattr(payload, "regime_confidence", 0) or 0)
        self._send_async(format_regime_change(old, regime, confidence))

    def _on_position_state(self, _subject: str, payload: Any) -> None:
        direction = int(getattr(payload, "direction", 0) or 0)
        qty = float(getattr(payload, "current_qty", 0) or 0)
        entry = float(getattr(payload, "entry_price", 0) or 0)

        # Detect direction change (open/close/flip)
        if direction == self._last_position_direction:
            return
        if not self._should_send("position_change"):
            return

        event_type = "CLOSE" if direction == 0 else "OPEN"
        if self._last_position_direction != 0 and direction != 0:
            event_type = "FLIP"
        self._last_position_direction = direction
        self._send_async(format_position_change(direction, qty, entry, event_type))

    def _on_risk_decision(self, _subject: str, payload: Any) -> None:
        flags = getattr(payload, "flags", []) or []
        if not isinstance(flags, list):
            return
        critical = [f for f in flags if f in ("KILL_SWITCH", "DD_STEP_3", "VOL_SHOCK", "SEVERE_CROWDING")]
        if not critical:
            return
        if not self._should_send("risk_alert"):
            return
        drawdown = float(getattr(payload, "drawdown", 0) or 0)
        equity = float(getattr(payload, "equity", 0) or 0)
        self._send_async(format_risk_alert(critical, drawdown, equity))

    def _on_execution_report(self, _subject: str, payload: Any) -> None:
        status = getattr(payload, "status", "")
        if status != "FILLED":
            return
        if not self._should_send("execution"):
            return
        self._send_async(
            format_execution(
                side=int(getattr(payload, "side", 0) or 0),
                qty=float(getattr(payload, "filled_qty", 0) or 0),
                avg_price=float(getattr(payload, "avg_price", 0) or 0),
                status=status,
                fee=float(getattr(payload, "fee", 0) or 0),
            )
        )

    # ── Rate limiting ──

    def _should_send(self, event_type: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(event_type, 0)
        if now - last < self._config.rate_limit_seconds:
            return False
        self._last_sent[event_type] = now
        return True

    # ── Helpers ──

    def _get_snapshot(self) -> dict[str, Any]:
        if self._snapshot_fn:
            return self._snapshot_fn()
        return {}

    def _send_async(self, text: str) -> None:
        """Fire-and-forget message send."""
        if not self._config.chat_id or not self._app:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_message(text))
        except RuntimeError:
            logger.debug("No event loop available for Telegram send")

    async def _send_message(self, text: str) -> None:
        try:
            await self._app.bot.send_message(
                chat_id=self._config.chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to send Telegram message")

    async def _daily_summary_loop(self) -> None:
        """Send daily P&L summary at configured UTC hour."""
        from datetime import UTC, datetime, timedelta

        while True:
            now = datetime.now(UTC)
            target = now.replace(
                hour=self._config.daily_summary_hour_utc,
                minute=0,
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return
            snap = self._get_snapshot()
            if snap:
                await self._send_message(format_daily_summary(snap))
