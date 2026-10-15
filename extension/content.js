// FaceFrame content script. Injected ONLY when the user clicks "Activate on this
// tab" in the extension popup. Lets the user confirm a <video> player, then
// extracts just the selected webcam panel from each decoded frame and transfers
// it (zero-copy) to the bridge iframe, which forwards it to 127.0.0.1.
//
// Extraction paths (selected per frame, with automatic fallback):
//   'videoframe'  WebCodecs VideoFrame(video).copyTo({rect}) -> decoder-native
//                 YUV 4:2:0 (I420/NV12) samples of the panel only, no browser
//                 colour conversion. Colour matrix/range sent alongside.
//   'canvas'      drawImage(video, panel) on a panel-sized canvas + getImageData
//                 -> RGBA (browser-converted).
// Neither path works on DRM-protected media or cross-origin (tainted) video; the
// exact error is reported and the app falls back to window capture.
(() => {
  'use strict';
  if (window.__faceframe && window.__faceframe.alive) { window.__faceframe.reopen(); return; }
  const G = globalThis.FFGeom;
  const HEADER = 64;
  const S = {
    alive: true, video: null, gen: 0, vw: 0, vh: 0, panel: null, fps: 30, maxInFlight: 2,
    inFlight: 0, seq: 0, port: null, connected: false, bridgeState: 'idle', lastSend: 0,
    overviewHz: 0, lastOverview: 0, overviewOnce: false, drops: 0, sent: 0, href: location.href,
    path: 'videoframe', pathError: '', lastCb: 0, rvfcHandle: 0, timer: 0, frameCanvas: null,
    ovCanvas: null, readbackMs: 0, state: 'choosing', text: '', iframe: null, nonce: null,
    selecting: false, colour: null,
  };
  window.__faceframe = { alive: true, reopen: () => { if (!S.video) showChooser(); } };

  // ------------------------------------------------------------------ UI (closed shadow root)
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;';
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `<style>
    .box{position:fixed;border:3px solid #ffd400;box-sizing:border-box;pointer-events:none}
    .lab{position:fixed;background:#111c;color:#fff;font:600 13px Segoe UI,sans-serif;padding:6px 8px;border-radius:6px;pointer-events:auto}
    .lab button,.badge button{margin-left:8px;font:600 12px Segoe UI;border-radius:5px;border:0;padding:3px 8px;cursor:pointer}
    .use{background:#6b3fd4;color:#fff}.x{background:#444;color:#fff}
    .badge{position:fixed;background:#000c;color:#fff;font:600 12px Segoe UI,sans-serif;padding:5px 8px;border-radius:14px;pointer-events:auto}
    .dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#e33;margin-right:6px;vertical-align:middle}
    .dot.idle{background:#aaa}.sel{position:fixed;cursor:crosshair;background:#0003;pointer-events:auto}
    .rb{position:fixed;border:2px dashed #ffd400;pointer-events:none}
  </style><div id="layer"></div>`;
  const layer = root.getElementById('layer');
  document.documentElement.appendChild(host);

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function setState(state, text) {
    S.state = state; S.text = text;
    chrome.runtime.sendMessage({ type: 'status', status: { state, text } }).catch(() => {});
    sendJSON({ type: 'status', state, text });
    renderBadge();
  }

  // ------------------------------------------------------------------ player chooser
  function candidates() {
    return [...document.querySelectorAll('video')].map(v => ({ v, r: v.getBoundingClientRect() }))
      .filter(c => c.r.width >= 80 && c.r.height >= 45)
      .sort((a, b) => (b.r.width * b.r.height) - (a.r.width * a.r.height));
  }

  function showChooser() {
    layer.innerHTML = '';
    const list = candidates();
    if (!list.length) { setState('error', 'No visible <video> element on this page'); return; }
    list.forEach((c, i) => {
      const b = el('div', 'box');
      Object.assign(b.style, { left: c.r.left + 'px', top: c.r.top + 'px', width: c.r.width + 'px', height: c.r.height + 'px' });
      const lab = el('div', 'lab', `Player ${i + 1}: ${c.v.videoWidth}x${c.v.videoHeight} ${c.v.paused ? '(paused)' : '(playing)'}`);
      Object.assign(lab.style, { left: (c.r.left + 8) + 'px', top: (c.r.top + 8) + 'px' });
      const use = el('button', 'use', 'Use this player'); use.onclick = () => choose(c.v);
      const x = el('button', 'x', 'Cancel'); x.onclick = () => shutdown('cancelled');
      lab.append(use, x); layer.append(b, lab);
    });
    setState('choosing', `Confirm the player (${list.length} video element${list.length > 1 ? 's' : ''} found)`);
  }

  function renderBadge() {
    if (!S.video || S.selecting) return;
    layer.innerHTML = '';
    const r = S.video.getBoundingClientRect();
    const b = el('div', 'badge');
    const dot = el('span', 'dot' + (S.state === 'capturing' ? '' : ' idle'));
    b.append(dot, document.createTextNode('FaceFrame: ' + (S.text || S.state)));
    const stop = el('button', 'x', 'Stop'); stop.onclick = () => shutdown('stopped by user');
    b.append(stop);
    Object.assign(b.style, { left: Math.max(4, r.left + 8) + 'px', top: Math.max(4, r.top + 8) + 'px' });
    layer.append(b);
  }

  // ------------------------------------------------------------------ in-page area selection
  function startAreaSelection() {
    if (!S.video) return;
    S.selecting = true;
    layer.innerHTML = '';
    const map = mapping();
    const vis = map.visible;
    const sel = el('div', 'sel');
    Object.assign(sel.style, { left: vis.x + 'px', top: vis.y + 'px', width: vis.w + 'px', height: vis.h + 'px' });
    const rb = el('div', 'rb'); rb.style.display = 'none';
    let a = null;
    sel.onpointerdown = e => { a = { x: e.clientX, y: e.clientY }; sel.setPointerCapture(e.pointerId); };
    sel.onpointermove = e => {
      if (!a) return;
      Object.assign(rb.style, { display: 'block', left: Math.min(a.x, e.clientX) + 'px', top: Math.min(a.y, e.clientY) + 'px',
        width: Math.abs(e.clientX - a.x) + 'px', height: Math.abs(e.clientY - a.y) + 'px' });
    };
    sel.onpointerup = e => {
      if (!a) return;
      const r = { x: Math.min(a.x, e.clientX), y: Math.min(a.y, e.clientY), w: Math.abs(e.clientX - a.x), h: Math.abs(e.clientY - a.y) };
      a = null; S.selecting = false; layer.innerHTML = '';
      if (r.w < 8 || r.h < 8) { renderBadge(); return; }
      const v = G.clampToVideo(mapping().toVideo(r), S.vw, S.vh);
      sendJSON({ type: 'panel-selected', rect: v, video: { w: S.vw, h: S.vh }, gen: S.gen });
      renderBadge();
    };
    layer.append(sel, rb);
    setState(S.state, 'Drag a rectangle around the webcam inset');
  }

  function mapping() {
    const v = S.video, cs = getComputedStyle(v), r = v.getBoundingClientRect();
    const px = s => parseFloat(s) || 0;
    const layout = { offsetWidth: v.offsetWidth, offsetHeight: v.offsetHeight, borderL: px(cs.borderLeftWidth),
      borderR: px(cs.borderRightWidth), borderT: px(cs.borderTopWidth), borderB: px(cs.borderBottomWidth),
      padL: px(cs.paddingLeft), padR: px(cs.paddingRight), padT: px(cs.paddingTop), padB: px(cs.paddingBottom) };
    const pos = (cs.objectPosition || '50% 50%').split(/\s+/);
    let matrix = null;
    if (cs.transform && cs.transform !== 'none') { try { matrix = new DOMMatrixReadOnly(cs.transform); } catch { /* ignore */ } }
    return G.videoMapping({ left: r.left, top: r.top, width: r.width, height: r.height }, layout, v.videoWidth, v.videoHeight,
      cs.objectFit || 'fill', pos[0], pos[1] || pos[0], matrix);
  }

  // ------------------------------------------------------------------ bridge channel
  async function openBridge() {
    const r = await chrome.runtime.sendMessage({ type: 'nonce' });
    if (!r || !r.ok) { setState('error', 'Could not obtain a channel nonce'); return; }
    const f = document.createElement('iframe');
    f.style.cssText = 'position:fixed;width:0;height:0;border:0;visibility:hidden;';
    f.src = chrome.runtime.getURL('bridge.html');
    S.iframe = f;
    f.onload = () => {
      const ch = new MessageChannel();
      S.port = ch.port1;
      S.port.onmessage = onBridge;
      // targetOrigin pinned to our extension origin: if a page script navigated
      // the frame elsewhere, the nonce/port are not delivered. (Verified: a frame
      // loaded via the use_dynamic_url GUID URL still has the static
      // chrome-extension://<id> origin; the GUID origin silently drops messages.)
      f.contentWindow.postMessage({ type: 'ff-init', nonce: r.nonce }, chrome.runtime.getURL('').slice(0, -1), [ch.port2]);
    };
    root.append(f);
  }

  function sendJSON(m) { if (S.port) S.port.postMessage(m); }

  function onBridge(ev) {
    const m = ev.data;
    if (!m || typeof m !== 'object') return;
    switch (m.type) {
      case 'bridge-status':
        S.bridgeState = m.state; S.connected = m.state === 'connected';
        if (!S.connected) { S.inFlight = 0; }
        setState(S.connected ? (S.panel ? 'capturing' : 'waiting-panel') : (m.state === 'unpaired' ? 'unpaired' : 'connecting'),
                 m.message || m.state);
        if (S.connected) sendMeta('connected');
        break;
      case 'set_panel': {
        const p = m.rect;
        if (!p || !(p.w > 0 && p.h > 0)) { S.panel = null; setState('waiting-panel', 'Select the webcam area in FaceFrame'); break; }
        S.panel = alignPanel(G.clampToVideo(p, S.vw, S.vh));
        setState('capturing', `Capturing ${S.panel.w}x${S.panel.h} of ${S.vw}x${S.vh}`);
        break;
      }
      case 'request_overview': S.overviewHz = Number(m.hz) || 0; S.overviewOnce = !S.overviewHz; break;
      case 'config':
        if (m.fps) S.fps = Math.max(1, Math.min(60, m.fps));
        if (m.max_in_flight) S.maxInFlight = Math.max(1, Math.min(4, m.max_in_flight));
        if (m.path === 'canvas' || m.path === 'videoframe') { S.path = m.path; }
        break;
      case 'ack': S.inFlight = Math.max(0, S.inFlight - 1); break;
      case 'drop': S.inFlight = Math.max(0, S.inFlight - 1); S.drops++; break;
      case 'select-area': startAreaSelection(); break;
      case 'stop': shutdown('stopped by FaceFrame'); break;
    }
  }

  function alignPanel(p) {
    // even-aligned (4:2:0 chroma) and inside the frame
    let x = p.x & ~1, y = p.y & ~1, w = p.w & ~1, h = p.h & ~1;
    w = Math.min(w, (S.vw - x) & ~1); h = Math.min(h, (S.vh - y) & ~1);
    return { x, y, w: Math.max(2, w), h: Math.max(2, h) };
  }

  function sendMeta(reason) {
    const v = S.video;
    sendJSON({ type: 'meta', reason, gen: S.gen, video: { w: v.videoWidth, h: v.videoHeight }, href: location.href,
      title: document.title, paused: v.paused, path: S.path, pathError: S.pathError,
      protectedMedia: !!v.mediaKeys, visibility: document.visibilityState, colour: S.colour,
      rvfc: typeof v.requestVideoFrameCallback === 'function', videoFrame: typeof VideoFrame === 'function' });
  }

  // ------------------------------------------------------------------ choose + run
  function choose(v) {
    S.video = v; S.vw = v.videoWidth; S.vh = v.videoHeight; S.gen++;
    layer.innerHTML = '';
    if (v.mediaKeys) setState('error', 'This video uses DRM (EME): pixels are protected; use window capture');
    openBridge();
    schedule();
    setState('connecting', 'Player confirmed; connecting to FaceFrame');
    S.watch = setInterval(watchdog, 500);
    chrome.runtime.onMessage.addListener(onRuntime);
  }

  function onRuntime(m, _s, reply) {
    if (m.type === 'stop') { shutdown('stopped from popup'); reply({ ok: true }); }
    if (m.type === 'select-area') { startAreaSelection(); reply({ ok: true }); }
  }

  function schedule() {
    const v = S.video;
    if (!S.alive || !v) return;
    if (typeof v.requestVideoFrameCallback === 'function') S.rvfcHandle = v.requestVideoFrameCallback(onFrame);
    else S.timer = setTimeout(() => onFrame(performance.now(), null), 1000 / S.fps);
  }

  function onFrame(now, md) {
    schedule();
    S.lastCb = performance.now();
    const v = S.video;
    if (!S.connected || !v || v.videoWidth === 0) return;
    if (v.videoWidth !== S.vw || v.videoHeight !== S.vh) { sizeChanged(); return; }
    const wantOverview = S.overviewOnce || (S.overviewHz > 0 && now - S.lastOverview >= 1000 / S.overviewHz);
    if (wantOverview && S.inFlight < S.maxInFlight) {
      S.overviewOnce = false; S.lastOverview = now;
      try { sendOverview(md); } catch (e) { setState('error', 'Overview readback failed: ' + e.name + ': ' + e.message); }
    }
    if (!S.panel) return;
    if (now - S.lastSend < 1000 / S.fps - 4) return;
    if (S.inFlight >= S.maxInFlight) { S.drops++; return; }
    S.lastSend = now;
    const tPresent = performance.timeOrigin + (md && md.presentationTime ? md.presentationTime : now);
    const mediaTime = md && typeof md.mediaTime === 'number' ? md.mediaTime : v.currentTime;
    try {
      sendPanel(tPresent, mediaTime);
    } catch (e) {
      const msg = e.name + ': ' + e.message;
      if (S.path === 'videoframe') { S.pathError = msg; S.path = 'canvas'; sendMeta('videoframe-failed'); return; }
      setState('blocked', 'Pixel access blocked: ' + msg);
      sendJSON({ type: 'blocked', error: msg, protectedMedia: !!v.mediaKeys });
    }
  }

  function header(buf, type, fmt, flags, w, h, r, tPresent, mediaTime, readbackMs, payload) {
    const d = new DataView(buf);
    d.setUint8(0, 0x46); d.setUint8(1, 0x46); d.setUint8(2, 0x76); d.setUint8(3, 0x31); // 'FFv1'
    d.setUint8(4, type); d.setUint8(5, fmt); d.setUint16(6, flags, true);
    d.setUint32(8, ++S.seq, true); d.setUint16(12, w, true); d.setUint16(14, h, true);
    d.setUint32(16, S.gen, true); d.setUint16(20, S.vw, true); d.setUint16(22, S.vh, true);
    d.setUint16(24, r.x, true); d.setUint16(26, r.y, true); d.setUint16(28, r.w, true); d.setUint16(30, r.h, true);
    d.setFloat64(32, tPresent, true); d.setFloat64(40, mediaTime, true); d.setFloat32(48, readbackMs, true);
    d.setUint32(52, payload, true);
  }

  function colourFlags(cs) {
    // bit0 fullRange; bits1-3 matrix: 1 bt709, 2 bt601, 3 bt2020
    if (!cs) return 0;
    const m = { bt709: 1, bt470bg: 2, smpte170m: 2, bt2020_ncl: 3 }[cs.matrix] || 0;
    return (cs.fullRange ? 1 : 0) | (m << 1);
  }

  function sendPanel(tPresent, mediaTime) {
    const r = S.panel, v = S.video;
    const t0 = performance.now();
    if (S.path === 'videoframe' && typeof VideoFrame === 'function') {
      const vf = new VideoFrame(v);
      try {
        const vr = vf.visibleRect;
        const fmt = vf.format;
        if (fmt !== 'I420' && fmt !== 'NV12') throw new Error('unsupported VideoFrame format ' + fmt);
        if (vf.displayWidth !== S.vw || vf.displayHeight !== S.vh || vr.width !== S.vw || vr.height !== S.vh) {
          throw new Error(`VideoFrame size ${vr.width}x${vr.height}/${vf.displayWidth}x${vf.displayHeight} != video ${S.vw}x${S.vh}`);
        }
        const rect = { x: vr.x + r.x, y: vr.y + r.y, width: r.w, height: r.h };
        const ySize = r.w * r.h;
        const layout = fmt === 'I420'
          ? [{ offset: 0, stride: r.w }, { offset: ySize, stride: r.w / 2 }, { offset: ySize + ySize / 4, stride: r.w / 2 }]
          : [{ offset: 0, stride: r.w }, { offset: ySize, stride: r.w }];
        const payload = ySize + ySize / 2;
        const buf = new ArrayBuffer(HEADER + payload);
        const cs = vf.colorSpace ? { matrix: vf.colorSpace.matrix, fullRange: vf.colorSpace.fullRange,
          primaries: vf.colorSpace.primaries, transfer: vf.colorSpace.transfer } : null;
        if (!S.colour || S.colour.matrix !== (cs && cs.matrix) || S.colour.transfer !== (cs && cs.transfer)) { S.colour = cs; sendMeta('colour'); }
        // copyTo is async; the frame must stay open until it resolves.
        vf.copyTo(new Uint8Array(buf, HEADER), { rect, layout }).then(() => {
          vf.close();
          S.readbackMs = performance.now() - t0;
          header(buf, 1, fmt === 'I420' ? 2 : 3, colourFlags(cs), r.w, r.h, r, tPresent, mediaTime, S.readbackMs, payload);
          post(buf);
        }, (e) => {
          vf.close();
          S.pathError = e.name + ': ' + e.message; S.path = 'canvas'; sendMeta('videoframe-failed');
        });
        return;
      } catch (e) { vf.close(); throw e; }
    }
    // canvas fallback (RGBA)
    if (!S.frameCanvas || S.frameCanvas.width !== r.w || S.frameCanvas.height !== r.h) {
      S.frameCanvas = new OffscreenCanvas(r.w, r.h);
      S.frameCtx = S.frameCanvas.getContext('2d', { willReadFrequently: true, alpha: false });
    }
    S.frameCtx.drawImage(v, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
    const img = S.frameCtx.getImageData(0, 0, r.w, r.h);  // throws SecurityError if tainted
    S.readbackMs = performance.now() - t0;
    const payload = img.data.byteLength;
    const buf = new ArrayBuffer(HEADER + payload);
    new Uint8Array(buf, HEADER).set(img.data);
    header(buf, 1, 1, 0, r.w, r.h, r, tPresent, mediaTime, S.readbackMs, payload);
    post(buf);
  }

  function sendOverview(md) {
    const v = S.video;
    const s = Math.min(1, 960 / S.vw);
    const w = Math.max(2, Math.round(S.vw * s)) & ~1, h = Math.max(2, Math.round(S.vh * s)) & ~1;
    if (!S.ovCanvas || S.ovCanvas.width !== w || S.ovCanvas.height !== h) {
      S.ovCanvas = new OffscreenCanvas(w, h);
      S.ovCtx = S.ovCanvas.getContext('2d', { willReadFrequently: true, alpha: false });
    }
    S.ovCtx.drawImage(v, 0, 0, w, h);
    const img = S.ovCtx.getImageData(0, 0, w, h);
    const buf = new ArrayBuffer(HEADER + img.data.byteLength);
    new Uint8Array(buf, HEADER).set(img.data);
    const tPresent = performance.timeOrigin + (md && md.presentationTime ? md.presentationTime : performance.now());
    header(buf, 2, 1, 0, w, h, { x: 0, y: 0, w: S.vw, h: S.vh }, tPresent, v.currentTime, 0, img.data.byteLength);
    post(buf);
  }

  function post(buf) {
    if (!S.port) return;
    S.inFlight++; S.sent++;
    S.port.postMessage(buf, [buf]);
  }

  function sizeChanged() {
    const ow = S.vw, oh = S.vh, v = S.video;
    S.vw = v.videoWidth; S.vh = v.videoHeight; S.gen++;
    // Same stream at a different rendition: the layout scales uniformly, so the
    // panel is rescaled proportionally. FaceFrame re-validates the content.
    if (S.panel && ow > 0 && oh > 0) S.panel = alignPanel(G.clampToVideo(G.rescaleRect(S.panel, ow, oh, S.vw, S.vh), S.vw, S.vh));
    sendJSON({ type: 'video-size-changed', old: { w: ow, h: oh }, video: { w: S.vw, h: S.vh }, gen: S.gen, panel: S.panel });
    sendMeta('size-changed');
  }

  function watchdog() {
    const v = S.video;
    if (location.href !== S.href) { sendJSON({ type: 'navigated', href: location.href }); shutdown('page navigated: activate again on the new page'); return; }
    if (!v || !v.isConnected) {
      // element replaced: rebind only if exactly one large playing video exists
      const list = candidates().filter(c => !c.v.paused && c.v.videoWidth > 0);
      if (list.length === 1) {
        S.video = list[0].v; S.vw = S.video.videoWidth; S.vh = S.video.videoHeight; S.gen++;
        sendJSON({ type: 'element-replaced', gen: S.gen, video: { w: S.vw, h: S.vh } });
        sendMeta('element-replaced');
        schedule();
        setState(S.state, 'Video element replaced; FaceFrame is re-validating the webcam area');
      } else {
        sendJSON({ type: 'element-lost', candidates: list.length });
        setState('error', 'Video element went away; open the extension popup and activate again');
        S.video = null;
      }
      return;
    }
    if (document.visibilityState === 'hidden') {
      if (S.state !== 'hidden') { setState('hidden', 'Tab hidden: the browser throttles video frames. Keep this tab selected and its window visible'); sendJSON({ type: 'hidden' }); }
    } else if (S.state === 'hidden') {
      setState(S.panel ? 'capturing' : 'waiting-panel', 'Tab visible again');
    }
    const idle = performance.now() - S.lastCb;
    if (S.connected && !v.paused && idle > 1500) sendJSON({ type: 'stalled', ms: Math.round(idle) });
    if (S.panel && S.state === 'capturing') renderBadge();
  }

  function shutdown(reason) {
    S.alive = false; window.__faceframe.alive = false;
    try { if (S.video && S.rvfcHandle && S.video.cancelVideoFrameCallback) S.video.cancelVideoFrameCallback(S.rvfcHandle); } catch { /* ignore */ }
    clearTimeout(S.timer); clearInterval(S.watch);
    sendJSON({ type: 'closed', reason });
    if (S.port) { S.port.postMessage({ type: 'close' }); S.port.close(); }
    chrome.runtime.sendMessage({ type: 'status', status: { state: 'stopped', text: reason } }).catch(() => {});
    try { chrome.runtime.onMessage.removeListener(onRuntime); } catch { /* ignore */ }
    host.remove();
  }

  showChooser();
})();
