// Abort saves client work; the sequence also protects against transports that ignore abort.
export function latestRequest(fetcher = fetch) {
  let sequence = 0;
  let controller;
  function cancel() { sequence++; controller?.abort(); }
  async function run(path, body, accept, reject) {
    cancel();
    const ticket = sequence;
    controller = new AbortController();
    try {
      const response = await fetcher(path, {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-ZenQuant-Request': '1' }, body,
      });
      const result = await response.json();
      if (ticket !== sequence) return;
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      accept(result);
    } catch (error) {
      if (ticket === sequence && error.name !== 'AbortError') reject(error.message || 'Request failed');
    }
  }
  return { run, cancel };
}
