// REST + WebSocket client for the ZenQuant dashboard API.

export interface SocketHandlers {
  onMessage: (payload: Record<string, unknown>) => void;
  onClose: () => void;
  onOpen?: () => void;
}

export async function fetchState(
  symbol: string,
  tf: string,
): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({ symbol, tf });
  const response = await fetch(`/api/state?${query.toString()}`);
  if (!response.ok) {
    throw new Error(`state request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchIndicatorHistory(
  symbol: string,
  tf: string,
): Promise<Record<string, unknown>[]> {
  const query = new URLSearchParams({ symbol, tf });
  const response = await fetch(
    `/api/indicators/history?${query.toString()}`,
  );
  if (!response.ok) {
    throw new Error(`history request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchPaperLiveScorecardLatest(
  symbol: string,
): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({ symbol });
  const response = await fetch(`/api/paper-live/scorecard/latest?${query.toString()}`);
  if (!response.ok) {
    throw new Error(`paper-live scorecard request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchPaperLiveScorecardHistory(
  limit = 50,
): Promise<Record<string, unknown>[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  const response = await fetch(`/api/paper-live/scorecard/history?${query.toString()}`);
  if (!response.ok) {
    throw new Error(`paper-live history request failed: ${response.status}`);
  }
  return response.json();
}

function buildWsUrl(path: string, symbol: string, tf: string): string {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({ symbol, tf });
  return `${protocol}://${window.location.host}${path}?${query.toString()}`;
}

export function connectStateSocket(
  symbol: string,
  tf: string,
  handlers: SocketHandlers,
): WebSocket {
  const socket = new WebSocket(buildWsUrl("/ws", symbol, tf));
  socket.onopen = () => handlers.onOpen?.();
  socket.onmessage = (event) => {
    try {
      handlers.onMessage(JSON.parse(event.data));
    } catch {
      // Ignore malformed payload
    }
  };
  socket.onclose = () => handlers.onClose();
  return socket;
}

export function connectIndicatorSocket(
  symbol: string,
  tf: string,
  handlers: SocketHandlers,
): WebSocket {
  const socket = new WebSocket(
    buildWsUrl("/ws/indicators", symbol, tf),
  );
  socket.onopen = () => handlers.onOpen?.();
  socket.onmessage = (event) => {
    try {
      handlers.onMessage(JSON.parse(event.data));
    } catch {
      // Ignore malformed payload
    }
  };
  socket.onclose = () => handlers.onClose();
  return socket;
}
