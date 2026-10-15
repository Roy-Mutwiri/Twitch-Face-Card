// FaceFrame Bridge service worker.
// Responsibilities: inject the content script on explicit user activation
// (activeTab), issue one-time channel nonces that authenticate the
// content-script <-> bridge-iframe MessagePort, and relay popup commands.
// It never sees pixels and never touches the pairing token.
'use strict';

const nonces = new Map(); // nonce -> { tabId, issued }
const tabState = new Map(); // tabId -> last status from the content script

function randomNonce() {
  const a = new Uint8Array(24);
  crypto.getRandomValues(a);
  return Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  // Our own extension page (bridge iframe). With use_dynamic_url its URL uses a
  // per-session GUID host, so check scheme + extension id, not the static URL.
  const fromExtensionPage = sender.id === chrome.runtime.id && typeof sender.url === 'string'
    && sender.url.startsWith('chrome-extension://') && sender.url.endsWith('/bridge.html');
  switch (msg && msg.type) {
    case 'nonce': {
      // content script asks for a channel nonce for its tab
      if (!sender.tab) { reply({ ok: false }); return; }
      const n = randomNonce();
      nonces.set(n, { tabId: sender.tab.id, frameId: sender.frameId, issued: Date.now() });
      reply({ ok: true, nonce: n });
      return;
    }
    case 'nonce-check': {
      // bridge iframe (extension page inside that tab) validates the nonce once
      const e = nonces.get(msg.nonce);
      nonces.delete(msg.nonce);
      const ok = !!e && fromExtensionPage && sender.tab && sender.tab.id === e.tabId && Date.now() - e.issued < 30000;
      reply({ ok });
      return;
    }
    case 'status': {
      if (sender.tab) tabState.set(sender.tab.id, { ...msg.status, t: Date.now() });
      return;
    }
    case 'popup-activate': {
      activate(msg.tabId).then(reply, e => reply({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
    case 'popup-state': {
      reply({ state: tabState.get(msg.tabId) || null });
      return;
    }
    case 'popup-command': {
      chrome.tabs.sendMessage(msg.tabId, { type: msg.command, arg: msg.arg }).then(
        r => reply(r || { ok: true }), e => reply({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
  }
});

async function activate(tabId) {
  // activeTab grant (user clicked the toolbar button on this tab) allows injection.
  await chrome.scripting.executeScript({ target: { tabId }, files: ['geometry.js', 'content.js'] });
  return { ok: true };
}

chrome.tabs.onRemoved.addListener(tabId => tabState.delete(tabId));
