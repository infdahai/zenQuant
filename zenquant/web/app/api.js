// Thin browser-side API helpers for the dashboard.

export async function fetchState(symbol, tf) {
  const query = new URLSearchParams({ symbol, tf });
  const response = await fetch(`/api/state?${query.toString()}`);
  if (!response.ok) {
    throw new Error(`state request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchIndicatorHistory(symbol, tf) {
  const query = new URLSearchParams({ symbol, tf });
  const response = await fetch(`/api/indicators/history?${query.toString()}`);
  if (!response.ok) {
    throw new Error(`history request failed: ${response.status}`);
  }
  return response.json();
}

export function connectStateSocket(symbol, tf, onMessage, onClose, onOpen) {
  // The state socket carries the full dashboard snapshot for a selection.
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({ symbol, tf });
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws?${query.toString()}`);

  socket.onopen = () => {
    if (typeof onOpen === "function") {
      onOpen();
    }
  };

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      onMessage(payload);
    } catch (_err) {
      // Ignore malformed payload and keep stream alive.
    }
  };

  socket.onclose = () => {
    onClose();
  };

  return socket;
}

export function connectIndicatorSocket(symbol, tf, onMessage, onClose, onOpen) {
  // The indicator socket is lighter and only emits newly published packs.
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({ symbol, tf });
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/indicators?${query.toString()}`);

  socket.onopen = () => {
    if (typeof onOpen === "function") {
      onOpen();
    }
  };

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      onMessage(payload);
    } catch (_err) {
      // Ignore malformed payload and keep stream alive.
    }
  };

  socket.onclose = () => {
    onClose();
  };

  return socket;
}
