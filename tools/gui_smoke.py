"""Scripted GUI run: real ControlWindow + OutputWindow, driven by timers instead of clicks.

  python tools/gui_smoke.py --title summit1g --panel 245,451,262,149 --seconds 30 [--mode enhanced]

Writes evidence/gui/*.png (control window grabs) and gui_status.json. While it
runs, tools/output_probe.py can capture "FaceFrame Output" from ANOTHER process.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from faceframe import settings as settings_mod  # noqa: E402
from faceframe.capture import windows as win  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.ui import app as appmod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="summit1g")
    ap.add_argument("--panel", default="245,451,262,149")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--mode", default="fast")
    ap.add_argument("--out", default="1280x720")
    ap.add_argument("--native", action="store_true", help="use the extension bridge instead of WGC")
    ap.add_argument("--settings", default=str(ROOT / "evidence" / "gui" / "settings_test.json"))
    ap.add_argument("--hide-control", action="store_true")
    ap.add_argument("--pattern", action="store_true", help="show the test pattern in the output window")
    ap.add_argument("--sample-every", type=float, default=0, help="seconds; write evidence/gui/soak_<mode>.jsonl")
    args = ap.parse_args()
    ev = ROOT / "evidence" / "gui"
    ev.mkdir(parents=True, exist_ok=True)

    appmod.setup_logging()
    sp = Path(args.settings)
    s, problems = settings_mod.load(sp)
    ow, oh = (int(v) for v in args.out.split("x"))
    s.output.width, s.output.height = ow, oh
    s.output.x, s.output.y = 40, 40
    s.quality.mode = args.mode
    # keep the test's settings separate from the user's real settings file
    settings_mod.save(s, sp)
    orig_save = settings_mod.save
    settings_mod.save = lambda st, path=None: orig_save(st, sp)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    appmod.apply_palette(app)
    w = appmod.ControlWindow(s, problems)
    w.move(3500, 60)
    w.show()
    t0 = time.time()
    log: list[str] = []

    def step_source():
        if args.native:
            w.use_native()
            log.append("native source started")
            return
        cands = [x for x in win.list_edge_windows() if args.title in x.title]
        w._start_wgc(cands[0].hwnd)
        log.append(f"WGC source {cands[0].title}")

    def step_panel():
        ov = w.pipeline.latest_overview
        if ov is None:
            QTimer.singleShot(300, step_panel)
            return
        w._apply_panel(Rect(*(float(v) for v in args.panel.split(","))), ov)
        log.append("panel applied")

    def step_output():
        w.btn_output.setChecked(True)
        log.append("output started")
        if args.pattern:
            w.chk_pattern.setChecked(True)
            log.append("test pattern on")

    def grab(name):
        w.grab().save(str(ev / f"{name}.png"))

    def step_tabs():
        grab("control_panel_tab")
        w.tabs.setCurrentIndex(0)
        QTimer.singleShot(600, lambda: grab("control_source_tab"))
        QTimer.singleShot(900, lambda: w.tabs.setCurrentIndex(2))
        QTimer.singleShot(2500, lambda: grab("control_compare_tab"))
        QTimer.singleShot(2700, lambda: w.tabs.setCurrentIndex(1))

    def step_hide():
        if args.hide_control:
            w.showMinimized()
            log.append("control window minimized")

    def finish():
        st = w.pipeline.status()
        st.update(w.output.stats())
        st["log"] = log
        st["elapsed"] = time.time() - t0
        (ev / f"gui_status_{args.mode}.json").write_text(json.dumps(st, indent=1, default=str))
        print(json.dumps(st, indent=1, default=str))
        w.close()

    samples_path = ev / f"soak_{args.mode}.jsonl"
    if args.sample_every:
        samples_path.write_text("")
        import psutil
        proc = psutil.Process()
        try:
            import pynvml
            pynvml.nvmlInit()
            nv = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            nv = None

        def sample():
            st = w.pipeline.status()
            st.update(w.output.stats())
            row = {"t": round(time.time() - t0, 1), "rss_mb": proc.memory_info().rss / 2 ** 20,
                   "cpu_pct": proc.cpu_percent(None), "threads": proc.num_threads()}
            if nv is not None:
                u = pynvml.nvmlDeviceGetUtilizationRates(nv)
                m = pynvml.nvmlDeviceGetMemoryInfo(nv)
                row.update(gpu_util=u.gpu, gpu_mem_used_mb=m.used / 2 ** 20,
                           gpu_temp_c=pynvml.nvmlDeviceGetTemperature(nv, 0))
            if w.engine is not None:
                mm = w.engine.memory_mb()
                row.update(torch_alloc_mb=mm[0], torch_reserved_mb=mm[1])
            for k in ("source_state", "track_state", "panel_validity", "capture_fps", "unique_source_fps",
                      "process_fps", "output_unique_fps", "latency_p50", "latency_p95", "latency_p99",
                      "paint_latency_p50", "paint_latency_p95", "paint_latency_p99", "queue_age_p95",
                      "dropped_process", "sr_ms", "mode_used", "fallback", "flicker_ratio", "size_ok"):
                row[k] = st.get(k)
            with open(samples_path, "a") as f:
                f.write(json.dumps(row, default=str) + chr(10))

        st_timer = QTimer()
        st_timer.timeout.connect(sample)
        QTimer.singleShot(5000, lambda: st_timer.start(int(args.sample_every * 1000)))
    QTimer.singleShot(500, step_source)
    QTimer.singleShot(2500, step_panel)
    QTimer.singleShot(3500, step_output)
    QTimer.singleShot(7000, step_tabs)
    QTimer.singleShot(11000, step_hide)
    QTimer.singleShot(int(args.seconds * 1000), finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
