// FaceFrame bridge iframe (extension origin, embedded invisibly in the activated tab).
// Owns the authenticated WebSocket to the local app. Receives pixel buffers from
// the content script over a transferred MessagePort (zero-copy ArrayBuffer
// transfer; chrome.runtime messaging would JSON-serialise and lose binary data).
// The pairing token lives in chrome.storage.local and never reaches the page.
'use strict';

let port = null;      // MessagePort to the content script
let ws = null;
let cfg = null;
let closed = false;
let reconnectTimer = null;
const MAX_BUFFERED = 6 * 1024 * 1024;   // bytes queued in the socket before we drop frames

function nowEpochMs() { return performance.timeOrigin + performance.now(); }

function toContent(m, transfer) { if (port) port.postMessage(m, transfer || []); }

window.addEventListener('message', async (e) => {
  if (port || !e.ports || !e.ports[0] || !e.data || e.data.type !== 'ff-init') return;
  // Only accept a port that comes with a nonce issued by our service worker to this tab.
  const r = await chrome.runtime.sendMessage({ type: 'nonce-check', nonce: String(e.data.nonce || '') });
  if (!r || !r.ok) { e.ports[0].close(); return; }
  port = e.ports[0];
  port.onmessage = onContentMessage;
  cfg = await chrome.storage.local.get({ port: 47811, token: '' });
  connect();   // pairing is automatic: FaceFrame verifies this extension's origin and sends the code
});

function connect() {
  if (closed) return;
  toContent({ type: 'bridge-status', state: 'connecting', message: 'Connecting to FaceFrame on 127.0.0.1:' + cfg.port });
  try {
    ws = new WebSocket('ws://127.0.0.1:' + Number(cfg.port) + '/ff');
  } catch (err) {
    toContent({ type: 'bridge-status', state: 'error', message: 'WebSocket error: ' + err.message });
    scheduleReconnect();
    return;
  }
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => ws.send(JSON.stringify({ type: 'hello', proto: 1, token: cfg.token, ua: navigator.userAgent }));
  ws.onmessage = (ev) => {
    if (typeof ev.data !== 'string') return;
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'ping') {
      // clock calibration: answer immediately with our epoch-ms timestamp
      ws.send(JSON.stringify({ type: 'pong', h: m.h, b: nowEpochMs() }));
      return;
    }
    if (m.type === 'paired' && typeof m.token === 'string') {
      cfg.token = m.token;
      chrome.storage.local.set({ token: m.token });   // extension-only storage, never the page
      return;
    }
    if (m.type === 'welcome') toContent({ type: 'bridge-status', state: 'connected', message: 'Connected to FaceFrame' });
    toContent(m); // set_panel, request_overview, config, ack, stop ...
  };
  ws.onclose = (ev) => {
    toContent({ type: 'bridge-status', state: 'disconnected', code: ev.code,
                message: ev.code === 4401 ? 'FaceFrame rejected the pairing code' : 'FaceFrame not reachable (is the app running with the bridge enabled?)' });
    ws = null;
    if (ev.code !== 4401) scheduleReconnect();
  };
  ws.onerror = () => {};
}

function scheduleReconnect() {
  if (closed || reconnectTimer) return;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 2000);
}

function onContentMessage(ev) {
  const m = ev.data;
  if (m instanceof ArrayBuffer) {
    if (!ws || ws.readyState !== WebSocket.OPEN) { toContent({ type: 'drop', reason: 'not-connected' }); return; }
    if (ws.bufferedAmount > MAX_BUFFERED) { toContent({ type: 'drop', reason: 'backpressure' }); return; }
    ws.send(m);
    return;
  }
  if (m && m.type === 'close') { closed = true; if (ws) ws.close(1000); return; }
  if (m && typeof m === 'object' && ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m));
}
