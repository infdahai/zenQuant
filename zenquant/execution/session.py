"""Admission for synchronous simulation; no venue report or account recovery API."""

from __future__ import annotations

import math
import os
from pathlib import Path

from zenquant.domain import ExecutionOrder, ExecutionReport
from zenquant.execution.gateway import ExecutionGateway
from zenquant.execution.simulated_gateway import SimulatedGateway
from zenquant.infra import EventBus


class SimulationSession:
    """One fresh simulated account, with one incremental result per order attempt.

    Partial results finish the simulated attempt: no outstanding venue order
    exists. Retrying the remaining target uses a new client order ID. Quantity,
    average price and fee describe only that attempt, never cumulative history.
    External, duplicate and late reports are rejected by the bus before fanout.

    A persistent session is deliberately NOT resumable. Its marker is synced
    before the first submission and remains after success, error or crash. A
    restart must refuse the same path; an explicitly new path means a new paper
    account, not restored positions. Replay uses an ephemeral fresh account.
    """

    def __init__(self, bus: EventBus, gateway: ExecutionGateway, path: Path | None = None) -> None:
        if type(gateway) not in (ExecutionGateway, SimulatedGateway):
            raise TypeError("only built-in simulation gateways are supported")
        self._path = path
        if path is not None and path.exists():
            raise RuntimeError(f"paper session cannot resume: {path}; account recovery is not implemented")
        self._bus = bus
        self._gateway = gateway
        self._publish_report = bus.claim_publisher("exec.order.report")
        self._order_ids: set[str] = set()
        self._started = False
        self.busy = False
        self.blocked = False

    def _start(self) -> None:
        if self._started:
            return
        if self._path is not None:
            # Require an existing parent: syncing only a newly created nested
            # directory would not durably establish its ancestors.
            with self._path.open("xb") as marker:
                marker.write(b"zenquant-simulation-v1: resume unsupported\n")
                marker.flush()
                os.fsync(marker.fileno())
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        self._started = True

    def submit(self, order: ExecutionOrder, **pricing) -> ExecutionReport:
        if self.blocked or self.busy:
            raise RuntimeError("simulation session is blocked or already submitting")
        self.busy = True
        try:
            self._start()
            if not order.client_order_id or order.client_order_id in self._order_ids:
                raise ValueError("simulation order identity must be unique")
            self._order_ids.add(order.client_order_id)
            self._bus.publish("exec.order.submit", order)
            report = self._gateway.submit_order(order, **pricing)
            self._validate(order, report)
            self._publish_report(report)
            return report
        except Exception:
            # Fanout failure can leave consumers at different offsets as well
            # as submission failure. Neither permits further orders or resume.
            self.blocked = True
            raise
        finally:
            self.busy = False

    @staticmethod
    def _validate(order: ExecutionOrder, report: ExecutionReport) -> None:
        if not isinstance(report, ExecutionReport):
            raise TypeError("simulator must return an ExecutionReport")
        if (
            report.client_order_id != order.client_order_id
            or report.symbol != order.symbol
            or report.side != order.side
            or report.side not in (-1, 1)
            or report.status not in {"FILLED", "PARTIALLY_FILLED", "REJECTED", "PENDING"}
            or not all(math.isfinite(value) for value in (report.filled_qty, report.avg_price, report.fee))
            or report.filled_qty < 0
            or report.filled_qty > order.quantity
            or report.fee < 0
            or report.avg_price < 0
            or (report.filled_qty > 0 and report.avg_price <= 0)
            or (report.status in {"REJECTED", "PENDING"} and (report.filled_qty != 0 or report.fee != 0))
        ):
            raise ValueError("invalid or mismatched simulation report")
