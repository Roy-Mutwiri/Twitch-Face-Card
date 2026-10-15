// FaceFrame geometry helpers (pure functions; unit-tested with node in tests/js).
// Maps a rectangle in client (CSS) coordinates over a <video> element into the
// video's intrinsic pixel coordinates (videoWidth x videoHeight), accounting for
// CSS box (border/padding), axis-aligned transforms/zoom and object-fit/position.
(function (root) {
  'use strict';

  function parsePosition(value, free) {
    // CSS object-position component -> offset in px given the free space.
    if (value === undefined || value === null || value === '') return free / 2;
    const v = String(value).trim();
    if (v === 'left' || v === 'top') return 0;
    if (v === 'right' || v === 'bottom') return free;
    if (v === 'center') return free / 2;
    if (v.endsWith('%')) return free * parseFloat(v) / 100;
    if (v.endsWith('px')) return parseFloat(v);
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : free / 2;
  }

  // Intrinsic content placement inside a content box of size (bw, bh).
  function objectFit(vw, vh, bw, bh, fit, posX, posY) {
    if (!(vw > 0 && vh > 0 && bw > 0 && bh > 0)) throw new Error('objectFit: non-positive size');
    let sx, sy;
    switch (fit || 'fill') {
      case 'fill': sx = bw / vw; sy = bh / vh; break;
      case 'contain': sx = sy = Math.min(bw / vw, bh / vh); break;
      case 'cover': sx = sy = Math.max(bw / vw, bh / vh); break;
      case 'none': sx = sy = 1; break;
      case 'scale-down': sx = sy = Math.min(1, Math.min(bw / vw, bh / vh)); break;
      default: throw new Error('objectFit: unknown fit ' + fit);
    }
    const dw = vw * sx, dh = vh * sy;
    return { sx, sy, ox: parsePosition(posX, bw - dw), oy: parsePosition(posY, bh - dh), dw, dh };
  }

  // box: { left, top, width, height }  = getBoundingClientRect() of the element (transformed)
  // layout: { offsetWidth, offsetHeight, borderL, borderT, padL, padT, padR, padB, borderR, borderB }
  // returns mapping {toVideo(rect), toClient(rect), scale, fit}
  function videoMapping(box, layout, vw, vh, fit, posX, posY, matrix) {
    if (matrix && (Math.abs(matrix.b) > 1e-6 || Math.abs(matrix.c) > 1e-6)) {
      throw new Error('rotated/skewed video transforms are not supported');
    }
    // axis-aligned transform/zoom: ratio of rendered box to layout box
    const kx = box.width / layout.offsetWidth;
    const ky = box.height / layout.offsetHeight;
    const cbw = layout.offsetWidth - layout.borderL - layout.borderR - layout.padL - layout.padR;
    const cbh = layout.offsetHeight - layout.borderT - layout.borderB - layout.padT - layout.padB;
    const cx0 = box.left + (layout.borderL + layout.padL) * kx;
    const cy0 = box.top + (layout.borderT + layout.padT) * ky;
    const f = objectFit(vw, vh, cbw, cbh, fit, posX, posY);
    function toVideo(r) {
      // client -> content box (layout px) -> intrinsic px
      const x = ((r.x - cx0) / kx - f.ox) / f.sx;
      const y = ((r.y - cy0) / ky - f.oy) / f.sy;
      return { x, y, w: r.w / kx / f.sx, h: r.h / ky / f.sy };
    }
    function toClient(r) {
      return { x: cx0 + (r.x * f.sx + f.ox) * kx, y: cy0 + (r.y * f.sy + f.oy) * ky,
               w: r.w * f.sx * kx, h: r.h * f.sy * ky };
    }
    // visible part of the video in client coords (letterbox excluded, clipped by box)
    const vis = toClient({ x: 0, y: 0, w: vw, h: vh });
    return { toVideo, toClient, visible: vis, fit: f, kx, ky };
  }

  function clampToVideo(r, vw, vh) {
    const x0 = Math.max(0, Math.min(vw, r.x)), y0 = Math.max(0, Math.min(vh, r.y));
    const x1 = Math.max(0, Math.min(vw, r.x + r.w)), y1 = Math.max(0, Math.min(vh, r.y + r.h));
    return { x: Math.round(x0), y: Math.round(y0), w: Math.round(x1 - x0), h: Math.round(y1 - y0) };
  }

  function rescaleRect(r, oldW, oldH, newW, newH) {
    const sx = newW / oldW, sy = newH / oldH;
    return { x: r.x * sx, y: r.y * sy, w: r.w * sx, h: r.h * sy };
  }

  const api = { parsePosition, objectFit, videoMapping, clampToVideo, rescaleRect };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FFGeom = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
