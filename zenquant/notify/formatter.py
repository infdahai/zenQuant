"""Format domain events into Telegram messages."""

from __future__ import annotations

from typing import Any


def format_regime_change(old_regime: str, new_regime: str, confidence: float) -> str:
    return f"*Regime Change*\n`{old_regime}` → `{new_regime}`\nConfidence: `{confidence:.2%}`"


def format_position_change(
    direction: int,
    qty: float,
    entry_price: float,
    event_type: str = "OPEN",
) -> str:
    dir_label = "Long" if direction > 0 else "Short" if direction < 0 else "Flat"
    if event_type == "CLOSE":
        return f"*Position Closed*\nPrevious: `{dir_label} {qty:.4f} BTC`"
    return f"*Position {event_type.title()}*\n`{dir_label} {qty:.4f} BTC` @ `${entry_price:,.2f}`"


def format_risk_alert(flags: list[str], drawdown: float, equity: float) -> str:
    flag_text = ", ".join(f"`{f}`" for f in flags)
    return f"*Risk Alert*\nFlags: {flag_text}\nDrawdown: `{drawdown:.2%}` | Equity: `${equity:,.2f}`"


def format_execution(
    side: int,
    qty: float,
    avg_price: float,
    status: str,
    fee: float,
) -> str:
    side_label = "Buy" if side > 0 else "Sell"
    return f"*Execution*\n`{side_label} {qty:.4f} BTC` @ `${avg_price:,.2f}`\nStatus: `{status}` | Fee: `${fee:.4f}`"


def format_daily_summary(snapshot: dict[str, Any]) -> str:
    pnl = snapshot.get("pnl_summary") or {}
    risk = snapshot.get("risk_snapshot") or {}
    state = snapshot.get("market_state") or {}
    pos = snapshot.get("position_state")

    lines = ["*Daily Summary*"]

    # PnL
    pnl_abs = pnl.get("pnl_abs", 0)
    pnl_pct = pnl.get("pnl_pct", 0)
    lines.append(f"PnL: `${pnl_abs:+,.2f}` (`{pnl_pct:+.2%}`)")

    # Equity
    equity = risk.get("equity", 0)
    dd = risk.get("drawdown", 0)
    lines.append(f"Equity: `${equity:,.2f}` | DD: `{dd:.2%}`")

    # Regime
    regime = state.get("regime", "UNKNOWN")
    lines.append(f"Regime: `{regime}`")

    # Position
    if pos and float(pos.get("current_qty", 0)) != 0:
        direction = "Long" if int(pos.get("direction", 0)) > 0 else "Short"
        lines.append(f"Position: `{direction} {float(pos.get('current_qty', 0)):.4f} BTC`")
    else:
        lines.append("Position: `Flat`")

    return "\n".join(lines)


def format_status(snapshot: dict[str, Any]) -> str:
    state = snapshot.get("market_state") or {}
    risk = snapshot.get("risk_snapshot") or {}
    pos = snapshot.get("position_state")
    pnl = snapshot.get("pnl_summary") or {}
    risk_dec = snapshot.get("risk_decision") or {}

    lines = ["*Current Status*"]
    lines.append(f"Regime: `{state.get('regime', 'UNKNOWN')}`")

    if pos and float(pos.get("current_qty", 0)) != 0:
        direction = "Long" if int(pos.get("direction", 0)) > 0 else "Short"
        lines.append(f"Position: `{direction} {float(pos.get('current_qty', 0)):.4f} BTC`")
    else:
        lines.append("Position: `Flat`")

    lines.append(f"Equity: `${float(risk.get('equity', 0)):,.2f}`")
    lines.append(f"Drawdown: `{float(risk.get('drawdown', 0)):.2%}`")

    flags = risk_dec.get("flags", [])
    active = [f for f in flags if f != "OK"] if isinstance(flags, list) else []
    lines.append(f"Risk: `{', '.join(active) if active else 'OK'}`")

    return "\n".join(lines)


def format_position_detail(snapshot: dict[str, Any]) -> str:
    pos = snapshot.get("position_state")
    if not pos or float(pos.get("current_qty", 0)) == 0:
        return "*Position*\n`Flat` — No open position"

    direction = "Long" if int(pos.get("direction", 0)) > 0 else "Short"
    lines = ["*Position Detail*"]
    lines.append(f"Direction: `{direction}`")
    lines.append(f"Entry: `${float(pos.get('entry_price', 0)):,.2f}`")
    lines.append(f"Qty: `{float(pos.get('current_qty', 0)):.4f} BTC`")

    stop = float(pos.get("trailing_stop", 0))
    if stop > 0:
        lines.append(f"Trailing Stop: `${stop:,.2f}`")

    lines.append(f"Bars Held: `{int(pos.get('bars_held', 0))}`")
    return "\n".join(lines)


def format_risk_detail(snapshot: dict[str, Any]) -> str:
    risk = snapshot.get("risk_snapshot") or {}
    risk_dec = snapshot.get("risk_decision") or {}

    lines = ["*Risk Detail*"]
    lines.append(f"Equity: `${float(risk.get('equity', 0)):,.2f}`")
    lines.append(f"HWM: `${float(risk.get('high_water_mark', 0)):,.2f}`")
    lines.append(f"Drawdown: `{float(risk.get('drawdown', 0)):.2%}`")
    lines.append(f"Leverage Cap: `{float(risk_dec.get('lev_cap', 0)):.1f}x`")

    flags = risk_dec.get("flags", [])
    active = [f for f in flags if f != "OK"] if isinstance(flags, list) else []
    lines.append(f"Active Flags: `{', '.join(active) if active else 'None'}`")

    return "\n".join(lines)


def format_pnl_detail(snapshot: dict[str, Any]) -> str:
    pnl = snapshot.get("pnl_summary") or {}
    lines = ["*PnL Detail*"]
    lines.append(f"Absolute: `${float(pnl.get('pnl_abs', 0)):+,.2f}`")
    lines.append(f"Percentage: `{float(pnl.get('pnl_pct', 0)):+.2%}`")
    lines.append(f"Starting Equity: `${float(pnl.get('starting_equity', 0)):,.2f}`")
    return "\n".join(lines)
