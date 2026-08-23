"""Shared accounting validation contracts."""

from __future__ import annotations

import math

from .models import ExecutionOrder, ExecutionReport

ExecutionReportIdentity = tuple[str, str, int]


def validate_execution_fee(fee: float) -> None:
    """Reject fee values that cannot be safely applied to cash."""
    if not math.isfinite(fee) or fee < 0.0:
        raise ValueError("fee must be finite and non-negative")


def validate_execution_report_accounting(report: ExecutionReport) -> None:
    """Reject report fields that cannot enter deterministic fill accounting."""
    validate_execution_fee(report.fee)
    if report.side not in {-1, 1}:
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(report.filled_qty) or report.filled_qty < 0.0:
        raise ValueError("filled_qty must be finite and non-negative")
    if report.filled_qty == 0.0:
        return
    if not math.isfinite(report.avg_price) or report.avg_price <= 0.0:
        raise ValueError("avg_price must be finite and positive for a fill")
    if not report.client_order_id:
        raise ValueError("client_order_id must be non-empty for a fill")


def validate_execution_report_for_order(report: ExecutionReport, order: ExecutionOrder) -> None:
    """Validate accounting fields and bind a report to its submitted order."""
    validate_execution_report_accounting(report)
    if report.client_order_id != order.client_order_id or report.symbol != order.symbol or report.side != order.side:
        raise ValueError("execution report does not match submitted order")


def execution_report_identity(report: ExecutionReport) -> ExecutionReportIdentity:
    """Return the stable business identity shared by direct and routed reports."""
    return (report.symbol, report.client_order_id, report.transaction_time_ns)
