// node tests for extension/geometry.js (display rect -> intrinsic video px)
'use strict';
const assert = require('assert');
const G = require('../../extension/geometry.js');

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);
const nearRect = (a, b, eps = 1e-6) => { near(a.x, b.x, eps); near(a.y, b.y, eps); near(a.w, b.w, eps); near(a.h, b.h, eps); };
const layout = (w, h, extra = {}) => Object.assign({ offsetWidth: w, offsetHeight: h, borderL: 0, borderR: 0, borderT: 0, borderB: 0,
  padL: 0, padR: 0, padT: 0, padB: 0 }, extra);
let n = 0;
function test(name, fn) { fn(); n++; console.log('ok', name); }

test('twitch-like: 1280x720 in 996x560.25 contain, offset in page', () => {
  const m = G.videoMapping({ left: 240, top: 50, width: 996, height: 560.25 }, layout(996, 560.25), 1280, 720, 'contain');
  const v = m.toVideo({ x: 240, y: 50, w: 996, h: 560.25 });
  nearRect(v, { x: 0, y: 0, w: 1280, h: 720 }, 1e-6);
});

test('letterbox: 16:9 video in 4:3 box (contain) maps bars outside the video', () => {
  const m = G.videoMapping({ left: 0, top: 0, width: 800, height: 600 }, layout(800, 600), 1920, 1080, 'contain');
  near(m.fit.oy, 75);
  const v = m.toVideo({ x: 0, y: 75, w: 800, h: 450 });
  nearRect(v, { x: 0, y: 0, w: 1920, h: 1080 });
  const c = G.clampToVideo(m.toVideo({ x: 0, y: 0, w: 100, h: 100 }), 1920, 1080);
  assert.strictEqual(c.y, 0);
});

test('pillarbox + object-position left', () => {
  const m = G.videoMapping({ left: 0, top: 0, width: 1000, height: 400 }, layout(1000, 400), 1600, 900, 'contain', '0%', '50%');
  near(m.fit.ox, 0);
  nearRect(m.toVideo({ x: 0, y: 0, w: 400 * 16 / 9, h: 400 }), { x: 0, y: 0, w: 1600, h: 900 }, 1e-6);
});

test('cover crops the video', () => {
  const m = G.videoMapping({ left: 0, top: 0, width: 500, height: 500 }, layout(500, 500), 1000, 500, 'cover');
  const v = m.toVideo({ x: 0, y: 0, w: 500, h: 500 });
  nearRect(v, { x: 250, y: 0, w: 500, h: 500 });
});

test('CSS transform scale / browser zoom (bounding box larger than layout box)', () => {
  // layout 640x360, rendered at 1.5x (transform: scale(1.5) or zoom)
  const m = G.videoMapping({ left: 100, top: 100, width: 960, height: 540 }, layout(640, 360), 1280, 720, 'contain');
  nearRect(m.toVideo({ x: 100, y: 100, w: 960, h: 540 }), { x: 0, y: 0, w: 1280, h: 720 });
  nearRect(m.toVideo({ x: 100 + 480, y: 100 + 270, w: 96, h: 54 }), { x: 640, y: 360, w: 128, h: 72 });
});

test('border and padding are excluded from the content box', () => {
  const m = G.videoMapping({ left: 0, top: 0, width: 660, height: 380 }, layout(660, 380, { borderL: 5, borderR: 5, borderT: 5, borderB: 5, padL: 5, padR: 5, padT: 5, padB: 5 }), 640, 360, 'fill');
  nearRect(m.toVideo({ x: 10, y: 10, w: 640, h: 360 }), { x: 0, y: 0, w: 640, h: 360 });
});

test('roundtrip toClient/toVideo', () => {
  const m = G.videoMapping({ left: 13, top: 7, width: 777, height: 555 }, layout(777, 555), 1920, 1080, 'contain');
  const r = { x: 100, y: 800, w: 400, h: 220 };
  nearRect(m.toVideo(m.toClient(r)), r, 1e-9);
});

test('rotation rejected', () => {
  assert.throws(() => G.videoMapping({ left: 0, top: 0, width: 10, height: 10 }, layout(10, 10), 10, 10, 'fill', '50%', '50%', { b: 0.5, c: -0.5 }));
});

test('rescale on rendition change', () => {
  nearRect(G.rescaleRect({ x: 0, y: 416, w: 350, h: 190 }, 1280, 720, 1920, 1080), { x: 0, y: 624, w: 525, h: 285 });
});

console.log(`${n} tests passed`);
