"""Native-video bridge validation in the ISOLATED Edge test instance.

Steps (all automated over CDP, none touch the user's Edge profile):
  1. (re)start the test Edge with a test copy of the extension (the copy adds a
     twitch.tv host permission ONLY because CDP cannot click the toolbar button
     that grants activeTab in the shipped extension)
  2. store the pairing code in the extension's storage (= user pasting it)
  3. inject geometry.js + content.js (= user pressing "Activate on this tab")
  4. click "Use this player" inside the closed shadow root via DOM.pierce
  5. run BridgeServer + Pipeline headlessly and collect measurements
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.capture.bridge import BridgeServer, BridgeSource  # noqa: E402
from faceframe.core.enhance import QualityMode, QualitySettings  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.core.pipeline import Pipeline, PipelineConfig  # noqa: E402
from tools.testbrowser import Cdp, Page, TestBrowser  # noqa: E402

SCR = Path(os.environ.get("FF_SCRATCH", Path(os.environ["TEMP"]) / "faceframe-test"))
TOKEN = "test-token-" + "x" * 40
PORT = 47899


def make_test_extension() -> Path:
    dst = SCR / "ext_test"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "extension", dst)
    man = json.loads((dst / "manifest.json").read_text())
    man["host_permissions"] = man["host_permissions"] + ["https://www.twitch.tv/*"]
    # test-only: allow the bridge iframe on the local controlled test page
    man["web_accessible_resources"][0]["matches"].append("http://127.0.0.1/*")
    (dst / "manifest.json").write_text(json.dumps(man, indent=1))
    return dst


def restart_browser(url: str, ext: Path) -> TestBrowser:
    try:
        import urllib.request
        v = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=2))
        Cdp(v["webSocketDebuggerUrl"]).call("Browser.close", timeout=5)
        time.sleep(3)
    except Exception:
        pass
    tb = TestBrowser(SCR / "edge-test-profile", window=(2600, 0, 1600, 1000), extension_dir=ext,
                     extra_args=["--disable-features=CalculateNativeWinOcclusion"])
    tb.start(url)
    return tb


def sw_page(tb: TestBrowser, ext_id: str) -> Page:
    for _ in range(50):
        for t in tb.targets():
            if t["type"] == "service_worker" and ext_id in t["url"]:
                return Page(t)
        time.sleep(0.2)
    raise RuntimeError("extension service worker not found")


def click_in_shadow(page: Page, selector_text: str) -> bool:
    doc = page.cdp.call("DOM.getDocument", {"depth": -1, "pierce": True})

    def walk(n):
        yield n
        for c in n.get("children", []) or []:
            yield from walk(c)
        for c in n.get("shadowRoots", []) or []:
            yield from walk(c)
        if n.get("contentDocument"):
            yield from walk(n["contentDocument"])

    for n in walk(doc["root"]):
        if n.get("nodeName") == "BUTTON":
            txt = "".join(c.get("nodeValue", "") for c in n.get("children", []) or [])
            if txt == selector_text:
                box = page.cdp.call("DOM.getBoxModel", {"nodeId": n["nodeId"]})["model"]["content"]
                x = (box[0] + box[2]) / 2
                y = (box[1] + box[5]) / 2
                for t in ("mousePressed", "mouseReleased"):
                    page.cdp.call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left",
                                                              "clickCount": 1})
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://www.twitch.tv/summit1g")
    ap.add_argument("--panel", default="", help="x,y,w,h in VIDEO pixels; empty = save overview and exit")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--path", default="videoframe", choices=["videoframe", "canvas"])
    ap.add_argument("--mode", default="fast")
    ap.add_argument("--reuse", action="store_true", help="reuse running test browser")
    ap.add_argument("--save", default=str(ROOT / "evidence" / "native"))
    ap.add_argument("--quality", default="", help="Twitch quality label to select first, e.g. 1080p60")
    ap.add_argument("--record", default="", help="directory: save every native panel frame as PNG (test clip)")
    args = ap.parse_args()
    save = Path(args.save)
    save.mkdir(parents=True, exist_ok=True)

    server = BridgeServer(PORT, TOKEN)
    server.start()
    assert not server.error, server.error
    q = QualitySettings(mode=QualityMode(args.mode))
    pipe = Pipeline(ROOT / "models", PipelineConfig(quality=q))
    if args.mode == "enhanced":
        from faceframe.core.sr_engine import SREngine
        eng = SREngine(q.sr_model, ROOT / "models")
        eng.load()
        pipe.set_sr_engine(eng)
    src = BridgeSource(server, path=args.path)
    pipe.set_source(src)

    ext = make_test_extension()
    if args.reuse:
        tb = TestBrowser(SCR / "edge-test-profile")
        tb.port = 9333
        import urllib.request
        v = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/version"))
        tb.browser = Cdp(v["webSocketDebuggerUrl"])
    else:
        tb = restart_browser(args.url, ext)
    ext_id = tb.load_extension() if not args.reuse else None
    print("extension id:", ext_id)
    page = tb.page("twitch.tv")
    time.sleep(12 if not args.reuse else 1)
    page.eval("""(()=>{const b=document.querySelector('[aria-label="Close modal"]'); if(b) b.click();})()""")
    if args.quality:
        print("quality:", select_quality(page, args.quality))
    sw = sw_page(tb, ext_id or "fgniljaehfphdhnpcknnipakaakgakha")
    if os.environ.get("FF_AUTOPAIR"):
        sw.eval(f"chrome.storage.local.clear().then(()=>chrome.storage.local.set({{port: {PORT}}}))")
    else:
        sw.eval(f"chrome.storage.local.set({{port: {PORT}, token: '{TOKEN}'}})")
    r = sw.eval("""(async()=>{const [t]=await chrome.tabs.query({url:'https://www.twitch.tv/*'});
        await chrome.scripting.executeScript({target:{tabId:t.id},files:['geometry.js','content.js']}); return t.id})()""")
    print("injected into tab", r)
    time.sleep(1.0)
    print("clicked Use this player:", click_in_shadow(page, "Use this player"))

    t0 = time.time()
    while not server.connected and time.time() - t0 < 10:
        time.sleep(0.2)
    print("connected:", server.connected, server.client_info.get("ua", "")[:60])
    time.sleep(1.5)
    ov = pipe.latest_overview
    if ov is not None:
        cv2.imwrite(str(save / "native_overview.png"), ov.pixels)
        print("overview", ov.pixels.shape, "video", ov.video_size, "scale", ov.scale)
    print("meta:", json.dumps({k: server.last_meta.get(k) for k in
                               ("video", "path", "pathError", "colour", "rvfc", "videoFrame", "protectedMedia")}))
    if not args.panel:
        pipe.shutdown(); server.stop()
        return 0

    pipe.set_panel(Rect(*(float(v) for v in args.panel.split(","))))
    if args.record:
        rec_dir = Path(args.record)
        rec_dir.mkdir(parents=True, exist_ok=True)
        if ov is not None:
            cv2.imwrite(str(rec_dir / "background_overview.png"), ov.pixels)
        orig_on_frame = pipe.on_frame
        meta_rows = []

        def recording_on_frame(f):
            n = len(meta_rows)
            cv2.imwrite(str(rec_dir / f"panel_{n:05d}.png"), f.pixels)
            meta_rows.append({"i": n, "seq": f.seq, "media_time": f.extra.get("media_time"),
                              "t_capture_ns": f.t_capture_ns, "conversion": f.extra.get("conversion"),
                              "panel_rect": f.panel_rect.as_tuple(), "video_size": f.video_size})
            orig_on_frame(f)
        src._sink = type("S", (), {"on_frame": staticmethod(recording_on_frame),
                                   "on_overview": staticmethod(pipe.on_overview),
                                   "on_status": staticmethod(pipe.on_status)})()
    t0 = time.time()
    selected = False
    while time.time() - t0 < args.seconds:
        time.sleep(0.05)
        if not selected:
            c = pipe.tracker.single_clear_candidate()
            if c is not None:
                pipe.select_detection(c.box)
                selected = True
                print(f"[{time.time() - t0:4.1f}s] auto-selected {c.box}")
    st = pipe.status()
    st.update(src.stats())
    st["conversion"] = getattr(src, "colour_conversion", "")
    st["meta"] = {k: server.last_meta.get(k) for k in ("video", "path", "pathError", "colour")}
    lf = pipe.latest_frame
    if lf is not None:
        cv2.imwrite(str(save / f"native_panel_{args.path}.png"), lf.pixels)
    o, _ = pipe.out_slot.peek()
    if o is not None:
        cv2.imwrite(str(save / f"native_output_{args.path}_{args.mode}.png"), o.image)
    if args.record:
        (Path(args.record) / "clip_meta.json").write_text(json.dumps(meta_rows, indent=0))
        st["recorded_frames"] = len(meta_rows)
    print(json.dumps(st, indent=1, default=str))
    (save / f"native_status_{args.path}_{args.mode}.json").write_text(json.dumps(st, indent=1, default=str))
    pipe.shutdown()
    server.stop()
    return 0


def select_quality(page: Page, label: str) -> str:
    """Use Twitch's normal player settings menu to pick a quality (best effort)."""
    js = """(async (label)=>{
      const sleep=ms=>new Promise(r=>setTimeout(r,ms));
      const q=s=>document.querySelector(s);
      const v=document.querySelector('video'); v.dispatchEvent(new MouseEvent('mousemove',{bubbles:true}));
      q('[data-a-target="player-settings-button"]').click(); await sleep(400);
      q('[data-a-target="player-settings-menu-item-quality"]').click(); await sleep(400);
      const opts=[...document.querySelectorAll('[data-a-target="player-settings-submenu-quality-option"]')];
      const names=opts.map(o=>o.innerText.trim());
      const o=opts.find(o=>o.innerText.trim().startsWith(label));
      if(o){ (o.querySelector('input')||o).click(); }
      await sleep(300); document.body.click();
      return JSON.stringify({names, chosen: !!o});
    })(%s)""" % json.dumps(label)
    return page.eval(js)


if __name__ == "__main__":
    raise SystemExit(main())
