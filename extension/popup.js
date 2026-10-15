'use strict';
const $ = (id) => document.getElementById(id);
let tab = null;

async function init() {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $('tab').textContent = tab ? (tab.title || '').slice(0, 80) : '';
  const s = await chrome.storage.local.get({ port: 47811, token: '' });
  $('port').value = s.port;
  $('token').value = s.token;
  refresh();
  setInterval(refresh, 1000);
}

async function refresh() {
  if (!tab) return;
  const r = await chrome.runtime.sendMessage({ type: 'popup-state', tabId: tab.id });
  const st = r && r.state;
  const el = $('state');
  el.className = 'state';
  if (!st) { el.textContent = 'Inactive on this tab'; return; }
  el.textContent = st.text || st.state;
  if (st.state === 'capturing') el.classList.add('active');
  if (st.state === 'error' || st.state === 'unpaired') el.classList.add('error');
}

$('activate').onclick = async () => {
  const r = await chrome.runtime.sendMessage({ type: 'popup-activate', tabId: tab.id });
  $('state').textContent = r && r.ok ? 'Activated: confirm the player on the page' : 'Activation failed: ' + (r && r.error);
};
$('stop').onclick = async () => {
  await chrome.runtime.sendMessage({ type: 'popup-command', tabId: tab.id, command: 'stop' });
  refresh();
};
$('save').onclick = async () => {
  const port = Math.max(1024, Math.min(65535, parseInt($('port').value, 10) || 47811));
  const token = $('token').value.trim();
  await chrome.storage.local.set({ port, token });
  $('saved').textContent = 'Saved';
  setTimeout(() => { $('saved').textContent = ''; }, 1500);
};
init();
