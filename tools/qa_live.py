"""Release QA against a LIVE Twitch channel (default: stylishnoob4).

  python tools/qa_live.py --channel stylishnoob4 --soak-min 10

Writes evidence/qa/qa_report.json and qa_report.md. Every check is measured on the
real stream through the real pipeline (direct source, GPU enhancement, fusion,
audio path with a silent device probe so nothing plays out loud).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.capture.audio import AudioOutput  # noqa: E402
from faceframe.capture.direct import DirectStreamSource, list_qualities  # noqa: E402
from faceframe.core.detector import YuNetDetector  # noqa: E402
from faceframe.core.enhance import QualityMode, QualitySettings  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.core.pipeline import Pipeline, PipelineConfig  # noqa: E402
from faceframe.core.sr_engine import SREngine  # noqa: E402

OUT = ROOT / "evidence" / "qa"
OUT.mkdir(parents=True, exist_ok=True)
REPORT: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": {}}


def check(name, ok, **data):
    REPORT["checks"][name] = {"pass": bool(ok), **data}
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {json.dumps(data, default=str)[:300]}", flush=True)


class SilentProbe:
    """Pulls real audio from the source like the device would, sends silence."""

    def __init__(self, src):
        self.src = src
        self.calls = 0
        self.loud = 0

    def read_audio(self, n, dac):
        a = self.src.read_audio(n, dac)
        self.calls += 1
        self.loud += int(np.abs(a).max() > 1e-3)
        return np.zeros_like(a)


def best_rendition(url):
    qs = [q for q in list_qualities(url) if q not in ("best", "audio_only")]
    def key(q):
        try:
            p, f = q.split("p")
            return int(p), int(f or 30)
        except Exception:
            return 0, 0
    return max(qs, key=key) if qs else None


def find_panel(src, pipe, det):
    """Locate the webcam inset: biggest face in a full frame, panel around it."""
    src.set_panel(Rect(0, 0, 1920, 1080))
    t0 = time.time()
    while time.time() - t0 < 15:
        f = pipe.latest_frame
        if f is not None and f.size == (1920, 1080):
            faces = det.detect(f.pixels)
            if faces:
                b = max(faces, key=lambda d: d.box.area).box
                w, h = b.h * 5.4, b.h * 3.0
                x = max(0, b.cx - w / 2)
                y = max(0, b.cy - h * 0.45)
                return Rect(x, y, min(w, 1920 - x), min(h, 1080 - y)), b
        time.sleep(0.3)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="stylishnoob4")
    ap.add_argument("--soak-min", type=float, default=10)
    a = ap.parse_args()
    url = f"https://www.twitch.tv/{a.channel}"
    proc = psutil.Process()
    det = YuNetDetector(ROOT / "models" / "face_detection_yunet_2023mar.onnx", work_long_side=1920)

    # ---------------------------------------------------------------- 1. acquisition
    expected = best_rendition(url)
    eng = SREngine("realesr-general-x4v3", ROOT / "models")
    eng.load()
    q = QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=0.7, sharpen=0.15, interpolation="lanczos")
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=1080, out_h=720, quality=q, temporal=True))
    pipe.set_sr_engine(eng)
    t0 = time.time()
    src = DirectStreamSource(url, "best")
    src.max_fps = 30
    pipe.set_source(src)
    while pipe.latest_overview is None and time.time() - t0 < 60:
        time.sleep(0.1)
    ttff = time.time() - t0
    check("stream_acquired_best_rendition", src.rendition == expected and pipe.latest_overview is not None,
          expected=expected, got=src.rendition, video=src.video_size, time_to_first_frame_s=round(ttff, 1),
          container=getattr(src, "container_format", ""))
    panel, face = find_panel(src, pipe, det)
    check("webcam_face_found", panel is not None, face=None if face is None else [round(v) for v in face.as_tuple()],
          panel=None if panel is None else [round(v) for v in panel.as_tuple()])
    if panel is None:
        return finish(pipe, None)
    pipe.set_panel(panel)
    t1 = time.time()
    while pipe.tracker.single_clear_candidate() is None and time.time() - t1 < 10:
        time.sleep(0.2)
    c = pipe.tracker.single_clear_candidate() or (max(pipe.tracker.candidates, key=lambda d: d.box.area)
                                                  if pipe.tracker.candidates else None)
    if c is not None:
        pipe.select_detection(c.box)
    check("person_auto_selected", c is not None, box=None if c is None else [round(v) for v in c.box.as_tuple()])

    ao = AudioOutput(SilentProbe(src))
    ao.start()
    check("audio_device_opened", ao.running, latency_ms=round(ao.latency_s * 1000, 1), error=ao.error,
          mixer_name=getattr(ao, "session_name", None))

    # ---------------------------------------------------------------- 2. soak
    samples = []
    rss0 = proc.memory_info().rss / 2 ** 20
    vram0 = eng.memory_mb()[1]
    end = time.time() + a.soak_min * 60
    while time.time() < end:
        time.sleep(10)
        st = pipe.status()
        samples.append({"t": round(time.time() - t1), "fps": st["process_fps"], "cap": st["capture_fps"],
                        "lat95": st["latency_p95"], "sr": st["sr_ms"], "fuse": st["fuse_ms"],
                        "mode": st["mode_used"], "track": st["track_state"], "area": st["panel_validity"],
                        "rss": proc.memory_info().rss / 2 ** 20, "vram": eng.memory_mb()[1],
                        "rebuf": src.rebuffers, "late": src.dropped_late, "aresync": src.audio_resyncs,
                        "threads": proc.num_threads(), "scale": st["scale"], "crop": st["crop_px"]})
        print("   soak", samples[-1], flush=True)
    fps = [s["fps"] for s in samples]
    rss_growth = samples[-1]["rss"] - samples[min(2, len(samples) - 1)]["rss"]
    modes = {s["mode"] for s in samples}
    tracking = sum(s["track"] == "TRACKING" for s in samples) / len(samples)
    valid = sum(s["area"] == "VALID" for s in samples) / len(samples)
    served = src.audio_served / max(1, src.audio_requested)
    check("soak_framerate", min(fps) >= 24 and np.median(fps) >= 27, median=round(float(np.median(fps)), 1),
          min=round(min(fps), 1), samples=len(samples))
    check("soak_latency_p95_under_100ms", max(s["lat95"] for s in samples) < 100,
          worst_p95_ms=round(max(s["lat95"] for s in samples), 1))
    check("soak_no_quality_fallback", modes == {"enhanced"} and not pipe.renderer.forced_fast,
          modes=sorted(modes), fallback=pipe.renderer.fallback_reason)
    check("soak_memory_stable", rss_growth < 150, rss_start_mb=round(rss0), rss_growth_mb=round(rss_growth, 1),
          vram_reserved_mb=round(samples[-1]["vram"]), vram_start_mb=round(vram0))
    check("soak_stream_stable", samples[-1]["rebuf"] <= 3, rebuffers=samples[-1]["rebuf"],
          late_drops=samples[-1]["late"])
    check("soak_tracking", tracking >= 0.8 and valid >= 0.8, tracking_fraction=round(tracking, 2),
          area_valid_fraction=round(valid, 2))
    check("audio_continuous", served >= 0.97 and src.audio_resyncs <= 5 and ao.underruns == 0,
          served_fraction=round(served, 3), resyncs=src.audio_resyncs, underruns=ao.underruns)
    check("threads_stable", samples[-1]["threads"] - samples[0]["threads"] <= 3,
          start=samples[0]["threads"], end=samples[-1]["threads"])
    REPORT["soak_samples"] = samples

    # ---------------------------------------------------------------- 3. quality evidence
    o, _ = pipe.out_slot.peek()
    cv2.imwrite(str(OUT / "live_output_best.png"), o.image)
    info = o.info
    check("output_exact_size", o.image.shape[:2] == (720, 1080), shape=list(o.image.shape))
    check("highest_quality_chain_active", info.mode_used.value == "enhanced" and pipe.cfg.temporal,
          mode=info.mode_used.value, fusion=pipe.cfg.temporal, crop=info.crop_size, scale=round(info.scale, 2),
          sr_ms=round(info.sr_ms, 1))

    # ---------------------------------------------------------------- 4. pause / resume
    v0 = pipe.out_slot.version
    src.pause()
    time.sleep(3)
    paused_new = pipe.out_slot.version - v0
    src.resume()
    time.sleep(4)
    check("pause_short_resume", paused_new <= 2 and pipe.status()["process_fps"] > 20, frames_while_paused=paused_new,
          fps_after=round(pipe.status()["process_fps"], 1))
    src.pause()
    time.sleep(10)
    t2 = time.time()
    src.resume()       # > 8 s on live: jumps back to the live edge
    ok = False
    while time.time() - t2 < 30:
        time.sleep(0.5)
        if pipe.status()["process_fps"] > 20 and src.state.value == "RUNNING":
            ok = True
            break
    check("pause_long_rejoins_live", ok, recovery_s=round(time.time() - t2, 1))

    # ---------------------------------------------------------------- 5. network drop injection
    time.sleep(3)
    t3 = time.time()
    src._close_relay()           # simulate the connection dropping
    time.sleep(1)
    rec = False
    while time.time() - t3 < 45:
        time.sleep(0.5)
        if src.state.value == "RUNNING" and pipe.status()["process_fps"] > 20:
            rec = True
            break
    check("network_drop_recovers", rec, recovery_s=round(time.time() - t3, 1))

    ao.stop()
    finish(pipe, src, eng)


def finish(pipe, src, eng=None):
    proc = psutil.Process()
    # ---------------------------------------------------------------- 6. stop/start cycles
    rss = []
    th = []
    for i in range(4):
        s2 = DirectStreamSource("https://www.twitch.tv/" + (sys.argv[sys.argv.index("--channel") + 1]
                                                            if "--channel" in sys.argv else "stylishnoob4"), "best")
        pipe.set_source(s2)
        t0 = time.time()
        while pipe.latest_overview is None and time.time() - t0 < 40:
            time.sleep(0.2)
        time.sleep(3)
        pipe.stop_source()
        time.sleep(1)
        rss.append(proc.memory_info().rss / 2 ** 20)
        th.append(proc.num_threads())
    check("stop_start_cycles_no_leak", rss[-1] - rss[0] < 80 and th[-1] - th[0] <= 4,
          rss_mb=[round(x) for x in rss], threads=th)

    # ---------------------------------------------------------------- 7. bad input
    s3 = DirectStreamSource("thischanneldoesnotexist_ff_qa_999", "best")
    states = []

    class Sink:
        def on_frame(s, f): pass
        def on_overview(s, o): pass
        def on_status(s, st): states.append(st.state.value + ": " + st.message[:80])
    s3.start(Sink())
    time.sleep(8)
    s3.stop()
    check("offline_channel_handled", any("STALLED" in x for x in states) and not any("RUNNING" in x for x in states),
          states=states[:4])
    pipe.shutdown()
    if eng is not None:
        eng.close()
    write_report()


def write_report():
    REPORT["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    REPORT["passed"] = sum(c["pass"] for c in REPORT["checks"].values())
    REPORT["total"] = len(REPORT["checks"])
    (OUT / "qa_report.json").write_text(json.dumps(REPORT, indent=1, default=str))
    lines = [f"# FaceFrame live QA report", "", f"Run {REPORT['started']} → {REPORT['finished']}  ",
             f"**{REPORT['passed']} / {REPORT['total']} checks passed**", "", "| Check | Result | Measurements |",
             "|---|---|---|"]
    for k, v in REPORT["checks"].items():
        d = {x: y for x, y in v.items() if x != "pass"}
        lines.append(f"| {k} | {'✅ pass' if v['pass'] else '❌ FAIL'} | `{json.dumps(d, default=str)[:260]}` |")
    (OUT / "qa_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{REPORT['passed']}/{REPORT['total']} passed -> {OUT / 'qa_report.md'}")


if __name__ == "__main__":
    main()
