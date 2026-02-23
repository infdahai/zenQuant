(function () {
  const state = {
    prices: [],
    bids: [],
    asks: [],
    signals: [],
    reports: [],
    weights: [],
    symbol: "BTC_USDT",
    exchange: "BINANCE",
    last_price: 0,
    spread: 0,
    position: 0,
    kill_switch_active: false,
  };

  const el = {
    symbolLabel: document.getElementById("symbolLabel"),
    exchangeLabel: document.getElementById("exchangeLabel"),
    lastPrice: document.getElementById("lastPrice"),
    spreadValue: document.getElementById("spreadValue"),
    positionValue: document.getElementById("positionValue"),
    killSwitchValue: document.getElementById("killSwitchValue"),
    orderBookRows: document.getElementById("orderBookRows"),
    signalList: document.getElementById("signalList"),
    reportList: document.getElementById("reportList"),
    weightList: document.getElementById("weightList"),
    priceCanvas: document.getElementById("priceCanvas"),
  };

  function init() {
    fetch("/api/state")
      .then((r) => r.json())
      .then((payload) => {
        applySnapshot(payload);
        render();
      })
      .catch(() => undefined);

    connectWebSocket();
    window.addEventListener("resize", () => renderChart());
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws`);

    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      applySnapshot(payload);
      render();
    };
    ws.onopen = () => {
      const heartbeat = setInterval(() => {
        if (ws.readyState !== WebSocket.OPEN) {
          clearInterval(heartbeat);
          return;
        }
        ws.send("ping");
      }, 15000);
    };
    ws.onclose = () => {
      setTimeout(connectWebSocket, 1200);
    };
  }

  function applySnapshot(payload) {
    Object.assign(state, payload);
  }

  function render() {
    renderHeader();
    renderChart();
    renderOrderBook();
    renderSignals();
    renderReports();
    renderWeights();
  }

  function renderHeader() {
    el.symbolLabel.textContent = state.symbol || "-";
    el.exchangeLabel.textContent = state.exchange || "-";
    el.lastPrice.textContent = formatNum(state.last_price, 2);
    el.spreadValue.textContent = formatNum(state.spread, 3);
    el.positionValue.textContent = formatNum(state.position, 4);
    el.killSwitchValue.textContent = state.kill_switch_active ? "TRIGGERED" : "SAFE";
    el.killSwitchValue.className = state.kill_switch_active ? "status-danger" : "status-safe";
  }

  function renderOrderBook() {
    const rows = [];
    const depth = Math.min(state.bids.length, state.asks.length, 16);
    for (let i = 0; i < depth; i += 1) {
      const bid = state.bids[i];
      const ask = state.asks[i];
      rows.push(`
        <div class="book-row">
          <span class="bid">${formatNum(bid.qty, 4)}</span>
          <span>${formatNum((bid.price + ask.price) / 2, 2)}</span>
          <span class="ask">${formatNum(ask.qty, 4)}</span>
        </div>
      `);
    }
    el.orderBookRows.innerHTML = rows.join("");
  }

  function renderSignals() {
    const cards = state.signals.slice(0, 8).map((item) => {
      const rawVal = item.type === 0 ? item.continuous_value : item.pattern_event;
      return cardHtml(item.factor_id, `Type ${item.type}`, `v=${formatNum(rawVal, 4)} ic=${formatNum(item.confidence_ic, 3)}`);
    });
    el.signalList.innerHTML = cards.join("");
  }

  function renderReports() {
    const cards = state.reports.slice(0, 8).map((item) => {
      const status = reportStatusText(item.status);
      const value = `status=${status} qty=${formatNum(item.filled_qty, 4)} vwap=${formatNum(item.vwap, 2)}`;
      return cardHtml(item.client_order_id, item.exchange_order_id, value);
    });
    el.reportList.innerHTML = cards.join("");
  }

  function renderWeights() {
    const cards = state.weights.slice(0, 8).map((item) => {
      const value = `w=${formatNum(item.weight, 4)} ic=${formatNum(item.ic_mean, 4)} p=${formatNum(item.orthogonal_penalty, 3)}`;
      return cardHtml(item.factor_id, item.symbol, value);
    });
    el.weightList.innerHTML = cards.join("");
  }

  function cardHtml(id, meta, value) {
    return `
      <article class="card">
        <div class="card-top">
          <span class="card-id">${escapeHtml(id)}</span>
          <span class="card-meta">${escapeHtml(meta)}</span>
        </div>
        <div class="card-value">${escapeHtml(value)}</div>
      </article>
    `;
  }

  function renderChart() {
    const canvas = el.priceCanvas;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(220, Math.floor(rect.height));
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const data = state.prices || [];
    if (data.length < 2) {
      drawGrid(ctx, width, height);
      return;
    }

    const values = data.map((d) => d.p);
    const minP = Math.min(...values);
    const maxP = Math.max(...values);
    const range = Math.max(1e-9, maxP - minP);
    const left = 16;
    const right = width - 16;
    const top = 18;
    const bottom = height - 20;
    const plotWidth = right - left;
    const plotHeight = bottom - top;

    drawGrid(ctx, width, height);

    const points = values.map((p, i) => {
      const x = left + (i / (values.length - 1)) * plotWidth;
      const y = top + ((maxP - p) / range) * plotHeight;
      return { x, y };
    });

    const gradient = ctx.createLinearGradient(0, top, 0, bottom);
    gradient.addColorStop(0, "rgba(19, 198, 167, 0.28)");
    gradient.addColorStop(1, "rgba(19, 198, 167, 0.02)");

    ctx.beginPath();
    ctx.moveTo(points[0].x, bottom);
    for (const pt of points) {
      ctx.lineTo(pt.x, pt.y);
    }
    ctx.lineTo(points[points.length - 1].x, bottom);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (const pt of points) {
      ctx.lineTo(pt.x, pt.y);
    }
    ctx.strokeStyle = "#25d7b3";
    ctx.lineWidth = 2.2;
    ctx.stroke();

    ctx.fillStyle = "#ffd9c1";
    ctx.font = "600 12px JetBrains Mono, monospace";
    ctx.fillText(`High ${formatNum(maxP, 2)}`, left, 14);
    ctx.fillText(`Low ${formatNum(minP, 2)}`, width - 110, 14);
  }

  function drawGrid(ctx, width, height) {
    ctx.fillStyle = "rgba(8, 14, 26, 0.65)";
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(65, 88, 133, 0.35)";
    ctx.lineWidth = 1;
    const lines = 6;
    for (let i = 1; i < lines; i += 1) {
      const y = (height / lines) * i;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
  }

  function reportStatusText(code) {
    if (code === 0) return "NEW";
    if (code === 1) return "PARTIAL";
    if (code === 2) return "FILLED";
    if (code === 3) return "REJECTED";
    return "UNKNOWN";
  }

  function formatNum(value, digits) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
      return "-";
    }
    return num.toFixed(digits);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  init();
})();

