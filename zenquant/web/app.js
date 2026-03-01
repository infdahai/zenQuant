(function () {
  const state = {
    symbol: "BTCUSDT_PERP",
    tf: "4h",
    snapshot: null,
    latestIndicator: null,
    indicatorSocket: null,
  };

  const el = {
    symbolSelect: document.getElementById("symbolSelect"),
    tfSelect: document.getElementById("tfSelect"),
    lastPrice: document.getElementById("lastPrice"),
    zenPulse: document.getElementById("zenPulse"),
    regime: document.getElementById("regime"),
    levCap: document.getElementById("levCap"),
    targetQty: document.getElementById("targetQty"),
    indicatorTable: document.getElementById("indicatorTable"),
    alertsList: document.getElementById("alertsList"),
    whyCard: document.getElementById("whyCard"),
    reportsList: document.getElementById("reportsList"),
    priceCanvas: document.getElementById("priceCanvas"),
  };

  function init() {
    el.symbolSelect.value = state.symbol;
    el.tfSelect.value = state.tf;

    el.symbolSelect.addEventListener("change", onSelectionChange);
    el.tfSelect.addEventListener("change", onSelectionChange);
    window.addEventListener("resize", drawChart);

    refreshState();
    connectIndicatorSocket();
    setInterval(refreshState, 2400);
  }

  function onSelectionChange() {
    state.symbol = el.symbolSelect.value;
    state.tf = el.tfSelect.value;
    refreshState();
    connectIndicatorSocket();
  }

  function refreshState() {
    const query = new URLSearchParams({ symbol: state.symbol, tf: state.tf });
    fetch(`/api/state?${query.toString()}`)
      .then((response) => response.json())
      .then((payload) => {
        state.snapshot = payload;
        if (payload.latest_indicator) {
          state.latestIndicator = payload.latest_indicator;
        }
        render();
      })
      .catch(() => undefined);
  }

  function connectIndicatorSocket() {
    if (state.indicatorSocket) {
      state.indicatorSocket.close();
    }
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const query = new URLSearchParams({ symbol: state.symbol, tf: state.tf });
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws/indicators?${query.toString()}`);
    state.indicatorSocket = socket;

    socket.onmessage = (event) => {
      state.latestIndicator = JSON.parse(event.data);
      render();
    };

    socket.onopen = () => {
      const heartbeat = setInterval(() => {
        if (socket.readyState !== WebSocket.OPEN) {
          clearInterval(heartbeat);
          return;
        }
        socket.send("ping");
      }, 15000);
    };

    socket.onclose = () => {
      if (state.indicatorSocket === socket) {
        setTimeout(connectIndicatorSocket, 1200);
      }
    };
  }

  function render() {
    const snapshot = state.snapshot || {};
    const indicator = state.latestIndicator || snapshot.latest_indicator || null;

    el.lastPrice.textContent = formatNum(snapshot.last_price, 2);
    el.targetQty.textContent = formatNum(snapshot.target_position, 5);

    const riskDecision = snapshot.risk_decision || null;
    el.levCap.textContent = riskDecision ? formatNum(riskDecision.lev_cap, 3) : "-";

    const reason = (snapshot.reasons || [])[0] || null;
    const regime = reason ? extractRegime(reason.reason_codes || []) : "-";
    el.regime.textContent = regime;

    if (indicator && indicator.values) {
      el.zenPulse.textContent = formatNum(indicator.values.zen_pulse, 1);
    } else {
      el.zenPulse.textContent = "-";
    }

    renderIndicatorTable(indicator);
    renderAlerts(snapshot.alerts || []);
    renderWhyCard(reason, riskDecision);
    renderReports(snapshot.reports || []);
    drawChart();
  }

  function extractRegime(reasonCodes) {
    const tag = reasonCodes.find((code) => String(code).startsWith("REGIME_"));
    if (!tag) {
      return "-";
    }
    return tag.replace("REGIME_", "");
  }

  function renderIndicatorTable(indicator) {
    if (!indicator || !indicator.values) {
      el.indicatorTable.innerHTML = `<div class="row"><span>waiting</span><span>-</span></div>`;
      return;
    }
    const quality = (indicator.quality_flags || []).join(", ");
    const rows = [
      ["funding_oi_weighted", indicator.values.funding_oi_weighted],
      ["funding_oi_z", indicator.values.funding_oi_z],
      ["cvd_slope", indicator.values.cvd_slope],
      ["ob_liq_z", indicator.values.ob_liq_z],
      ["overall_ob_liq_z", indicator.values.overall_ob_liq_z],
      ["realized_vol", indicator.values.realized_vol],
      ["atr", indicator.values.atr],
      ["book_age_ms", indicator.values.book_age_ms],
      ["oi_coverage", indicator.values.oi_coverage],
      ["quality_flags", quality],
    ];

    el.indicatorTable.innerHTML = rows
      .map(([name, value]) => `<div class="row"><span>${escapeHtml(name)}</span><span>${escapeHtml(formatValue(value))}</span></div>`)
      .join("");
  }

  function renderAlerts(alerts) {
    if (!alerts.length) {
      el.alertsList.innerHTML = `<article class="card"><strong>OK</strong> no active alerts</article>`;
      return;
    }

    el.alertsList.innerHTML = alerts
      .slice(0, 8)
      .map((item) => {
        const flags = (item.flags || []).join(", ");
        return `
          <article class="card">
            <div><strong>${escapeHtml(item.type || "ALERT")}</strong> - ${escapeHtml(item.symbol || "-")}</div>
            <div>${escapeHtml(flags)}</div>
          </article>
        `;
      })
      .join("");
  }

  function renderWhyCard(reason, riskDecision) {
    if (!reason) {
      el.whyCard.innerHTML = "Waiting for first bar close intent...";
      return;
    }

    const reasonCodes = (reason.reason_codes || []).join(", ");
    const riskFlags = riskDecision ? (riskDecision.flags || []).join(", ") : "OK";
    el.whyCard.innerHTML = `
      <div>${escapeHtml(reason.summary || "-")}</div>
      <div style="margin-top:8px;color:#8fb0ca;">reason_codes: ${escapeHtml(reasonCodes || "-")}</div>
      <div style="margin-top:8px;color:#8fb0ca;">risk_flags: ${escapeHtml(riskFlags || "-")}</div>
    `;
  }

  function renderReports(reports) {
    if (!reports.length) {
      el.reportsList.innerHTML = `<article class="card">No fills yet.</article>`;
      return;
    }

    el.reportsList.innerHTML = reports
      .slice(0, 10)
      .map((report) => {
        const cls = report.status === "FILLED" ? "status-ok" : "status-bad";
        return `
          <article class="card">
            <div><span class="${cls}">${escapeHtml(report.status || "-")}</span> ${escapeHtml(report.symbol || "-")}</div>
            <div>qty=${escapeHtml(formatNum(report.filled_qty, 5))} price=${escapeHtml(formatNum(report.avg_price, 2))}</div>
          </article>
        `;
      })
      .join("");
  }

  function drawChart() {
    const canvas = el.priceCanvas;
    if (!canvas) {
      return;
    }

    const candles = ((state.snapshot || {}).candles || []).slice(-120);
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(220, Math.floor(rect.height));
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    drawGrid(ctx, width, height);

    if (candles.length < 2) {
      return;
    }

    const closes = candles.map((c) => Number(c.close));
    const minP = Math.min(...closes);
    const maxP = Math.max(...closes);
    const range = Math.max(1e-9, maxP - minP);

    const left = 12;
    const top = 14;
    const right = width - 12;
    const bottom = height - 16;
    const plotW = right - left;
    const plotH = bottom - top;

    const points = closes.map((price, index) => {
      const x = left + (index / (closes.length - 1)) * plotW;
      const y = top + ((maxP - price) / range) * plotH;
      return { x, y };
    });

    const fill = ctx.createLinearGradient(0, top, 0, bottom);
    fill.addColorStop(0, "rgba(21, 216, 181, 0.3)");
    fill.addColorStop(1, "rgba(21, 216, 181, 0.03)");

    ctx.beginPath();
    ctx.moveTo(points[0].x, bottom);
    points.forEach((point) => ctx.lineTo(point.x, point.y));
    ctx.lineTo(points[points.length - 1].x, bottom);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach((point) => ctx.lineTo(point.x, point.y));
    ctx.strokeStyle = "#15d8b5";
    ctx.lineWidth = 2.1;
    ctx.stroke();

    ctx.fillStyle = "#bfe6ff";
    ctx.font = "600 12px JetBrains Mono, monospace";
    ctx.fillText(`H ${formatNum(maxP, 2)}  L ${formatNum(minP, 2)}`, left, 12);
  }

  function drawGrid(ctx, width, height) {
    ctx.fillStyle = "rgba(10, 18, 28, 0.8)";
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(58, 92, 120, 0.35)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 6; i += 1) {
      const y = (height / 6) * i;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
  }

  function formatNum(value, digits) {
    const n = Number(value);
    if (!Number.isFinite(n)) {
      return "-";
    }
    return n.toFixed(digits);
  }

  function formatValue(value) {
    if (typeof value === "number") {
      if (!Number.isFinite(value)) {
        return "-";
      }
      if (Math.abs(value) >= 1000) {
        return value.toFixed(2);
      }
      return value.toFixed(4);
    }
    if (value === null || value === undefined) {
      return "-";
    }
    return String(value);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  init();
})();
