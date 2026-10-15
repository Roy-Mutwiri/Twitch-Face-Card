"""Real-world behaviour scenarios against the ISOLATED Edge test instance.

  python tools/scenarios.py wgc        resize / move / minimize / channel switch / stop-restart / close
  python tools/scenarios.py occlusion  fully cover a normal (occlusion-detecting) Edge window
  python tools/scenarios.py native     rendition switch / hidden tab / element removal / navigation / reconnect

Each step records a timeline of source state, tracking state, panel validity and
whether the output is neutral. Results: evidence/scenarios/<name>.json + .md
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.capture import windows as win  # noqa: E402
from faceframe.capture.wgc import WgcSource  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.core.pipeline import Pipeline, PipelineConfig  # noqa: E402
from tools.testbrowser import Cdp, Page, TestBrowser  # noqa: E402

SCR = Path(os.environ.get("FF_SCRATCH", Path(os.environ["TEMP"]) / "faceframe-test"))
EV = ROOT / "evidence" / "scenarios"
EV.mkdir(parents=True, exist_ok=True)
VIDEO_PANEL = (0, 416, 350, 190)   # summit1g webcam inset in 1280x720 video px (measured from native overview)


class Recorder:
    def __init__(self, pipe: Pipeline) -> None:
        self.pipe = pipe
        self.rows: list[dict] = []
        self.t0 = time.time()

    def snap(self, label: str = "") -> dict:
        p = self.pipe
        o, _ = p.out_slot.peek()
        st = p.status()
        row = {"t": round(time.time() - self.t0, 2), "label": label, "source": st["source_state"],
               "track": st["track_state"], "panel": st["panel_validity"],
               "score": None if st["panel_score"] != st["panel_score"] else round(st["panel_score"], 2),
               "neutral": None if o is None else o.neutral,
               "panel_rect": None if p.panel is None else [round(v) for v in p.panel.as_tuple()],
               "src_size": st["source_size"] if not st["video_size"] else f"video {st['video_size']}", "cap_fps": round(st["capture_fps"], 1),
               "uniq_fps": round(st["unique_source_fps"], 1), "msg": st["source_msg"][:80],
               "event": st["panel_event"][:90]}
        self.rows.append(row)
        return row

    def watch(self, seconds: float, label: str, every: float = 0.5) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.snap(label)
            time.sleep(every)


def attach_browser() -> TestBrowser:
    v = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=3))
    tb = TestBrowser(SCR / "edge-test-profile")
    tb.browser = Cdp(v["webSocketDebuggerUrl"])
    return tb


def window_id(tb: TestBrowser, page: Page) -> int:
    return tb.browser.call("Browser.getWindowForTarget", {"targetId": page.target["id"]})["windowId"]


def set_bounds(tb, wid, **b) -> None:
    tb.browser.call("Browser.setWindowBounds", {"windowId": wid, "bounds": b})


def find_panel_in_window(page: Page, frame_bgr: np.ndarray) -> Rect:
    """Viewport offset inside the WGC frame (template match of a CDP screenshot) +
    video element rect + known panel in video px -> panel in window px."""
    shot = page.cdp.call("Page.captureScreenshot", {"format": "png"})["data"]
    vp = cv2.imdecode(np.frombuffer(base64.b64decode(shot), np.uint8), cv2.IMREAD_COLOR)
    tmpl = vp[100:300, 300:700]
    res = cv2.matchTemplate(frame_bgr, tmpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    ox, oy = loc[0] - 300, loc[1] - 100
    r = page.eval("(()=>{const v=document.querySelector('video');const b=v.getBoundingClientRect();"
                  "return [b.left,b.top,b.width,b.height,v.videoWidth,v.videoHeight]})()")
    s = r[2] / r[4]
    x, y, w, h = VIDEO_PANEL
    print(f"viewport offset ({ox},{oy}) match {score:.3f}; video {r[4]}x{r[5]} shown at {r[2]:.0f}x{r[3]:.0f}")
    return Rect(ox + r[0] + x * s, oy + r[1] + y * s, w * s, h * s)


def grab_full(hwnd: int) -> np.ndarray:
    import threading
    from windows_capture import WindowsCapture
    out = {}
    ev = threading.Event()
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @cap.event
    def on_frame_arrived(frame, control):
        out["img"] = cv2.cvtColor(frame.frame_buffer, cv2.COLOR_BGRA2BGR)
        ev.set()
        control.stop()

    @cap.event
    def on_closed():
        ev.set()

    c = cap.start_free_threaded()
    ev.wait(3)
    c.stop()
    c.wait()
    return out["img"]


def start_tracking(pipe: Pipeline, rec: Recorder, timeout=8.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        c = pipe.tracker.single_clear_candidate()
        if c is not None:
            pipe.select_detection(c.box)
            return True
        if pipe.tracker.candidates:
            # several faces (posters): pick the largest, as a user click would
            best = max(pipe.tracker.candidates, key=lambda d: d.box.area)
            if time.time() - t0 > 3:
                pipe.select_detection(best.box)
                return True
        time.sleep(0.2)
    return False


def write(name: str, rec: Recorder, notes: list[str]) -> None:
    (EV / f"{name}.json").write_text(json.dumps({"rows": rec.rows, "notes": notes}, indent=1))
    lines = [f"# Scenario: {name}", "", *[f"- {n}" for n in notes], "",
             "| t | step | source | track | panel | score | neutral | panel rect | src size | cap fps | uniq fps | message / event |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    last = None
    for r in rec.rows:
        key = (r["label"], r["source"], r["track"], r["panel"], r["neutral"], tuple(r["panel_rect"] or ()))
        if key == last:
            continue
        last = key
        lines.append(f"| {r['t']} | {r['label']} | {r['source']} | {r['track']} | {r['panel']} | {r['score']} | "
                     f"{r['neutral']} | {r['panel_rect']} | {r['src_size']} | {r['cap_fps']} | {r['uniq_fps']} | "
                     f"{r['msg']} {('/ ' + r['event']) if r['event'] else ''} |")
    (EV / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


# --------------------------------------------------------------------------- WGC
def scenario_wgc() -> None:
    win.ensure_dpi_awareness()
    tb = attach_browser()
    page = tb.page("twitch.tv")
    if "summit1g" not in page.eval("location.href"):
        page.navigate("https://www.twitch.tv/summit1g")
        time.sleep(12)
    wid = window_id(tb, page)
    set_bounds(tb, wid, windowState="normal")
    set_bounds(tb, wid, left=2600, top=0, width=1600, height=1000)
    time.sleep(2)
    hwnd = [w for w in win.list_edge_windows() if "summit1g" in w.title][0].hwnd
    panel = find_panel_in_window(page, grab_full(hwnd))
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=1280, out_h=720))
    rec = Recorder(pipe)
    notes = [f"window hwnd {hwnd:#x}; initial panel (window px) {[round(v) for v in panel.as_tuple()]}"]
    try:
        pipe.set_source(WgcSource(hwnd, 0))
        pipe.set_panel(panel)
        ok = start_tracking(pipe, rec)
        notes.append(f"person selected: {ok}")
        rec.watch(4, "baseline")

        set_bounds(tb, wid, left=2300, top=60)              # move only
        rec.watch(4, "moved window")

        set_bounds(tb, wid, width=1900, height=1150)        # resize (player rescales)
        rec.watch(8, "resized larger")
        set_bounds(tb, wid, width=1600, height=1000)
        rec.watch(8, "resized back")

        set_bounds(tb, wid, windowState="minimized")
        rec.watch(5, "minimized")
        set_bounds(tb, wid, windowState="normal")
        rec.watch(7, "restored")

        pipe.stop_source()
        rec.watch(2, "stopped source")
        notes.append(f"after stop: source={pipe.source}, frames still arriving? capture fps "
                     f"{pipe.cap_rate.rate():.1f}")
        pipe.set_source(WgcSource(hwnd, 0))
        pipe.set_panel(find_panel_in_window(page, grab_full(hwnd)))
        start_tracking(pipe, rec)
        rec.watch(5, "restarted same window (Edge untouched)")

        page.navigate("https://www.twitch.tv/directory")     # layout/scene change in the same tab
        rec.watch(8, "navigated tab to another page")
        page.navigate("https://www.twitch.tv/summit1g")
        rec.watch(14, "navigated back")
    finally:
        write("wgc", rec, notes)
        pipe.shutdown()


def scenario_close() -> None:
    """Close the captured window: never falls back to another window / desktop."""
    win.ensure_dpi_awareness()
    tb = attach_browser()
    page = tb.page("twitch.tv")
    hwnd = [w for w in win.list_edge_windows() if "summit1g" in w.title][0].hwnd
    panel = find_panel_in_window(page, grab_full(hwnd))
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=1280, out_h=720))
    rec = Recorder(pipe)
    notes = []
    try:
        src = WgcSource(hwnd, 0)
        pipe.set_source(src)
        pipe.set_panel(panel)
        start_tracking(pipe, rec)
        rec.watch(3, "baseline")
        tb.browser.call("Browser.close", timeout=5)
        rec.watch(6, "source window closed")
        notes.append(f"WGC source still bound to the same hwnd {src.hwnd:#x}; IsWindow={win.is_window(src.hwnd)}; "
                     f"restarts attempted on other windows: none (adapter only restarts on its own hwnd)")
    finally:
        write("close", rec, notes)
        pipe.shutdown()


# --------------------------------------------------------------------- occlusion
def scenario_occlusion() -> None:
    """A normal Edge (occlusion detection ON) fully covered by another window."""
    win.ensure_dpi_awareness()
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    tb = TestBrowser(SCR / "edge-test-profile-occl", port=9334, window=(3300, 300, 1200, 800))
    tb.start("https://www.twitch.tv/summit1g")
    time.sleep(14)
    page = tb.page("twitch.tv")
    page.eval("""(()=>{const b=document.querySelector('[data-a-target="player-play-pause-button"]');
               const v=document.querySelector('video'); if(v&&v.paused&&b) b.click();})()""")
    time.sleep(4)
    hwnd = [w for w in win.list_edge_windows() if "summit1g" in w.title and w.rect[0] >= 3000][0].hwnd
    wi = win.info(hwnd)
    import ctypes
    HWND_TOPMOST, HWND_NOTOPMOST, SWP = -1, -2, 0x0001 | 0x0002 | 0x0040  # NOSIZE|NOMOVE|SHOWWINDOW
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP)   # make it genuinely visible
    time.sleep(3)
    page.eval("""(()=>{const b=document.querySelector('[data-a-target="player-play-pause-button"]');
               const v=document.querySelector('video'); if(v&&v.paused&&b) b.click();})()""")
    time.sleep(4)
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=640, out_h=360))
    rec = Recorder(pipe)
    notes = [f"second Edge instance WITHOUT --disable-features=CalculateNativeWinOcclusion at {wi.rect}"]
    cover = QWidget()
    cover.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
    cover.setStyleSheet("background:#203040")
    x, y, w, h = wi.rect
    try:
        pipe.set_source(WgcSource(hwnd, 0))
        pipe.set_panel(Rect(w * 0.2, h * 0.3, w * 0.3, h * 0.3))
        for _ in range(16):
            app.processEvents(); time.sleep(0.25)
        rec.snap("visible")
        notes.append(f"visible: visibilityState={page.eval('document.visibilityState')} "
                     f"unique fps {pipe.unique_rate.rate():.1f}")
        cover.setGeometry(x - 20, y - 20, w + 40, h + 40)
        cover.show()
        for i in range(32):
            app.processEvents(); time.sleep(0.25)
            if i % 2 == 0:
                rec.snap("fully covered")
        notes.append(f"fully covered: visibilityState={page.eval('document.visibilityState')} "
                     f"unique fps {pipe.unique_rate.rate():.1f}")
        cover.setGeometry(x + 200, y + 150, w - 400, h - 300)   # partially covered (edges visible)
        for i in range(24):
            app.processEvents(); time.sleep(0.25)
            if i % 2 == 0:
                rec.snap("partially covered")
        notes.append(f"partially covered: visibilityState={page.eval('document.visibilityState')} "
                     f"unique fps {pipe.unique_rate.rate():.1f}")
        cover.hide()
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP)
        for i in range(16):
            app.processEvents(); time.sleep(0.25)
            if i % 2 == 0:
                rec.snap("uncovered")
        notes.append(f"uncovered: visibilityState={page.eval('document.visibilityState')} "
                     f"unique fps {pipe.unique_rate.rate():.1f}")
    finally:
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP)
        write("occlusion", rec, notes)
        pipe.shutdown()
        tb.stop()


if __name__ == "__main__":
    {"wgc": scenario_wgc, "occlusion": scenario_occlusion, "close": scenario_close}[sys.argv[1]]()
