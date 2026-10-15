"""Deterministic scenarios on the local controlled test stream (tools/testpage +
evidence/clip), in the isolated Edge test instance.

  python tools/controlled.py wgc
  python tools/controlled.py native
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.capture import windows as win  # noqa: E402
from faceframe.capture.bridge import BridgeServer, BridgeSource  # noqa: E402
from faceframe.capture.wgc import WgcSource  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.core.pipeline import Pipeline, PipelineConfig  # noqa: E402
from tools.native_test import PORT, TOKEN, click_in_shadow, make_test_extension, sw_page  # noqa: E402
from tools.scenarios import Recorder, find_panel_in_window, grab_full, set_bounds, window_id, write  # noqa: E402
from tools.testbrowser import Cdp, Page, TestBrowser  # noqa: E402

SCR = Path(os.environ.get("FF_SCRATCH", Path(os.environ["TEMP"]) / "faceframe-test"))
URL = "http://127.0.0.1:8765/index.html"


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith("/clip/"):
            return str(ROOT / "evidence" / "clip" / p[len("/clip/"):])
        return str(ROOT / "tools" / "testpage" / p.lstrip("/"))

    def log_message(self, *a):
        pass


def serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def start_browser(with_ext: bool) -> tuple[TestBrowser, Page, str | None]:
    import urllib.request
    try:
        v = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=2))
        Cdp(v["webSocketDebuggerUrl"]).call("Browser.close", timeout=5)
        time.sleep(3)
    except Exception:
        pass
    ext = make_test_extension() if with_ext else None
    tb = TestBrowser(SCR / "edge-test-profile", window=(2600, 0, 1600, 1000), extension_dir=ext,
                     extra_args=["--disable-features=CalculateNativeWinOcclusion"])
    tb.start(URL)
    ext_id = tb.load_extension() if with_ext else None
    page = tb.page("127.0.0.1:8765")
    for _ in range(100):
        if page.eval("window.ffReady === true"):
            break
        time.sleep(0.3)
    time.sleep(1)
    return tb, page, ext_id


def select_face(pipe, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pipe.tracker.candidates:
            best = max(pipe.tracker.candidates, key=lambda d: d.box.area)  # the streamer, not the poster
            pipe.select_detection(best.box)
            return best.box
        time.sleep(0.2)
    return None


def track_quality(rec: Recorder, pipe, label_prefix=""):
    pass


def scenario_wgc():
    win.ensure_dpi_awareness()
    srv = serve()
    tb, page, _ = start_browser(False)
    hwnd = [w for w in win.list_edge_windows() if "FaceFrame Test Stream" in w.title][0].hwnd
    wid = window_id(tb, page)
    panel = find_panel_in_window(page, grab_full(hwnd))
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=1280, out_h=720))
    rec = Recorder(pipe)
    notes = [f"controlled clip page, WGC hwnd {hwnd:#x}, panel {[round(v) for v in panel.as_tuple()]}"]
    targets = []
    try:
        pipe.set_source(WgcSource(hwnd, 0))
        pipe.set_panel(panel)
        notes.append(f"selected face {select_face(pipe)}")
        t_end = time.time() + 20
        while time.time() < t_end:       # 20 s of tracking the moving clip
            rec.snap("baseline 20 s")
            if pipe.tracker.target is not None:
                targets.append(pipe.tracker.target.cx)
            time.sleep(0.25)
        page.eval("window.scrollBy(0, 120)")
        rec.watch(5, "page scrolled 120 px")
        page.eval("window.scrollTo(0, 0)")
        rec.watch(5, "scrolled back")
        set_bounds(tb, wid, width=1900, height=1150)
        rec.watch(9, "window resized larger")
        set_bounds(tb, wid, width=1600, height=1000)
        rec.watch(9, "window resized back")
        page.eval("ffSetAd(true)")
        rec.watch(6, "ad break (inset replaced)")
        page.eval("ffSetAd(false)")
        rec.watch(6, "ad over")
        set_bounds(tb, wid, windowState="minimized")
        rec.watch(4, "minimized")
        set_bounds(tb, wid, windowState="normal")
        rec.watch(6, "restored")
        tb.browser.call("Browser.close", timeout=5)
        rec.watch(5, "source window closed")
        notes.append(f"tracked face centre x range over 20 s baseline: {min(targets):.0f}..{max(targets):.0f} "
                     f"(panel width {pipe.panel.w if pipe.panel else '?'}; poster face is at x>220)")
        notes.append(f"stale detections rejected: {pipe.tracker.stale_rejected}; "
                     f"switch-guard rejections (other faces ignored): {pipe.tracker.switch_guard_rejections}")
    finally:
        write("controlled_wgc", rec, notes)
        pipe.shutdown()
        srv.shutdown()


def scenario_native():
    srv = serve()
    server = BridgeServer(PORT, TOKEN)
    server.start()
    tb, page, ext_id = start_browser(True)
    sw = sw_page(tb, ext_id)
    sw.eval(f"chrome.storage.local.set({{port: {PORT}, token: '{TOKEN}'}})")
    sw.eval("""(async()=>{const [t]=await chrome.tabs.query({url:'http://127.0.0.1:8765/*'});
        await chrome.scripting.executeScript({target:{tabId:t.id},files:['geometry.js','content.js']}); return t.id})()""")
    time.sleep(1)
    click_in_shadow(page, "Use this player")
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=1280, out_h=720))
    src = BridgeSource(server)
    pipe.set_source(src)
    rec = Recorder(pipe)
    notes = []
    t0 = time.time()
    while not server.connected and time.time() - t0 < 10:
        time.sleep(0.2)
    time.sleep(1.5)
    try:
        pipe.set_panel(Rect(0, 416, 350, 190))
        notes.append(f"connected {server.connected}; selected face {select_face(pipe)}; "
                     f"meta path={server.last_meta.get('path')} pathError={server.last_meta.get('pathError')}")
        rec.watch(6, "baseline")
        notes.append(f"conversion: {getattr(src, 'colour_conversion', '')}")
        page.eval("ffSetRendition(1920, 1080)")
        rec.watch(6, "rendition 1280x720 -> 1920x1080")
        notes.append(f"panel after rendition switch: {pipe.panel}")
        page.eval("ffSetRendition(1280, 720)")
        rec.watch(6, "rendition back to 1280x720")
        page.eval("ffSetAd(true)")
        rec.watch(6, "ad break")
        page.eval("ffSetAd(false)")
        rec.watch(6, "ad over")
        page.eval("ffReplaceVideo()")
        rec.watch(6, "video element replaced")
        r = tb.browser.call("Target.createTarget", {"url": "about:blank"})   # another tab in front
        rec.watch(5, "another tab selected (Twitch tab hidden)")
        tb.browser.call("Target.closeTarget", {"targetId": r["targetId"]})
        page.cdp.call("Page.bringToFront")
        rec.watch(5, "tab selected again")
        server.stop()
        rec.watch(3, "FaceFrame bridge server stopped")
        server2 = BridgeServer(PORT, TOKEN)
        server2.start()
        src.server = server2
        server2.listener = src
        rec.watch(8, "bridge server restarted (auto-reconnect)")
        notes.append(f"reconnected: {server2.connected}")
        page.eval("ffRemoveVideo()")
        rec.watch(4, "video element removed")
        notes.append(f"native stats: {json.dumps(src.stats(), default=str)}")
        server2.stop()
    finally:
        write("controlled_native", rec, notes)
        pipe.shutdown()
        srv.shutdown()
        tb.stop()


if __name__ == "__main__":
    {"wgc": scenario_wgc, "native": scenario_native}[sys.argv[1]]()
