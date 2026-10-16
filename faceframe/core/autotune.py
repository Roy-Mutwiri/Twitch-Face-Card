"""Automatic performance: read this PC's specs, pick the best quality tier it can sustain,
and keep adjusting while it runs.

Tiers (best first). Every tier keeps the output at the configured size and frame rate;
lower tiers spend less CPU/GPU per frame:

  Ultra     GPU AI enhancement + multi-frame fusion, Lanczos      (NVIDIA GPU)
  High      GPU AI enhancement, Lanczos                           (NVIDIA GPU)
  Balanced  multi-frame fusion + Lanczos + sharpening             (strong CPU)
  Light     Bicubic + source-size sharpening, slower detection
  Saver     as Light, stream capped at 720p (decoding 1080p60 is the biggest CPU cost)
  Minimal   as Saver, stream capped at 480p, slowest detection

The probe picks a starting tier from the hardware and a ~1 s benchmark of the real
per-frame work. The Governor then watches the live pipeline: if output frames fall
behind or the decoder drops frames, it steps down one tier; with sustained headroom it
steps back up (up to Balanced on CPU-only PCs, Ultra with a GPU; not again soon after a failed step-up).
"""

from __future__ import annotations

import logging
import os
import platform
import time
from dataclasses import asdict, dataclass, field

log = logging.getLogger("faceframe.autotune")

BENCH_VERSION = 2


@dataclass(frozen=True)
class Tier:
    name: str
    mode: str                 # enhanced | fast
    temporal: bool
    interpolation: str        # lanczos | cubic
    light: bool               # sharpen at source size
    detect_hz: float
    max_height: int | None    # stream rendition cap
    preview_hz: int           # UI preview refresh
    summary: str


TIERS: list[Tier] = [
    Tier("Ultra", "enhanced", True, "lanczos", False, 12, None, 30, "GPU AI enhancement + multi-frame fusion"),
    Tier("High", "enhanced", False, "lanczos", False, 10, None, 30, "GPU AI enhancement"),
    Tier("Balanced", "fast", True, "lanczos", False, 10, None, 20, "multi-frame fusion + Lanczos + sharpening"),
    Tier("Light", "fast", False, "cubic", True, 8, None, 15, "bicubic + light sharpening, full stream quality"),
    Tier("Saver", "fast", False, "cubic", True, 6, 720, 12, "light processing, stream capped at 720p"),
    Tier("Minimal", "fast", False, "cubic", True, 4, 480, 8, "lightest processing, stream capped at 480p"),
]
NAMES = [t.name for t in TIERS]


def tier_index(name: str, default: int = 2) -> int:
    return NAMES.index(name) if name in NAMES else default


@dataclass
class SystemSpec:
    cpu: str = ""
    cores: int = 0                 # physical
    threads: int = 0               # logical
    ram_gb: float = 0.0
    gpu: str = ""
    vram_gb: float = 0.0
    gpu_sms: int = 0
    cuda: bool = False
    # benchmark (ms per output frame of the real operations, this PC, all cores)
    ms_balanced: float = 0.0       # fusion + Lanczos 1080x720 + output sharpening
    ms_light: float = 0.0          # bicubic + source sharpening
    ms_detect: float = 0.0         # one face detection on a webcam panel
    ms_yuv: float = 0.0            # 1080p YUV->BGR of a panel-sized region x2 + overview
    bench_version: int = BENCH_VERSION
    probed_at: str = ""
    extra: dict = field(default_factory=dict)

    def describe(self) -> str:
        g = f"{self.gpu} {self.vram_gb:.0f} GB" if self.gpu else "no NVIDIA GPU"
        return f"{self.cpu or 'CPU'} · {self.cores} cores / {self.threads} threads · {self.ram_gb:.0f} GB RAM · {g}"

    def same_hardware(self, other: "SystemSpec") -> bool:
        return (self.cpu, self.threads, self.gpu, round(self.ram_gb)) == \
            (other.cpu, other.threads, other.gpu, round(other.ram_gb)) and other.bench_version == BENCH_VERSION


def _cpu_name() -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except Exception:
        return platform.processor() or ""


def probe_hardware(use_gpu: bool = True) -> SystemSpec:
    import psutil
    sp = SystemSpec(cpu=_cpu_name(), cores=psutil.cpu_count(logical=False) or 1,
                    threads=psutil.cpu_count(logical=True) or 1,
                    ram_gb=psutil.virtual_memory().total / 2 ** 30, probed_at=time.strftime("%Y-%m-%d %H:%M"))
    if use_gpu:
        try:
            import torch
            if torch.cuda.is_available():
                p = torch.cuda.get_device_properties(0)
                sp.gpu, sp.vram_gb, sp.gpu_sms, sp.cuda = p.name, p.total_memory / 2 ** 30, p.multi_processor_count, True
        except Exception as e:  # no torch (CPU-only install), driver problems
            sp.extra["gpu_error"] = str(e)[:200]
    return sp


def benchmark(sp: SystemSpec, models_dir=None, rounds: int = 6) -> SystemSpec:
    """~0.5–1.5 s: the real per-frame operations on synthetic webcam-sized data."""
    import cv2
    import numpy as np

    from ..capture.yuv import yuv420_to_bgr
    from .enhance import unsharp
    from .temporal import TemporalFuser

    rng = np.random.default_rng(1)
    base = cv2.GaussianBlur(rng.integers(0, 255, (225, 460, 3), np.uint8), (0, 0), 2)
    frames = [np.roll(base, i, axis=1) for i in range(4)]
    crop = np.ascontiguousarray(base[0:220, 60:390])

    def timed(fn, n=rounds):
        fn()                                    # warm-up (allocations, OpenCV dispatch)
        t = time.perf_counter()
        for i in range(n):
            fn(i)
        return (time.perf_counter() - t) * 1000.0 / n

    fz = TemporalFuser(0.75)

    def balanced(i=0):
        p = fz.process(frames[i % 4])
        c = p[0:220, 60:390]
        img = cv2.resize(c, (1080, 720), interpolation=cv2.INTER_LANCZOS4)
        unsharp(img, 0.3, 1.6)

    def light(i=0):
        cv2.resize(unsharp(crop, 0.3, 0.7), (1080, 720), interpolation=cv2.INTER_CUBIC)

    y = rng.integers(16, 236, (1080, 1920), np.uint8)
    u = rng.integers(16, 241, (540, 960), np.uint8)

    def yuv(i=0):
        yuv420_to_bgr(y[855:1080, 18:478], u[427:540, 9:239], u[427:540, 9:239])
        yuv420_to_bgr(cv2.resize(y, (960, 540), interpolation=cv2.INTER_AREA),
                      cv2.resize(u, (480, 270), interpolation=cv2.INTER_AREA),
                      cv2.resize(u, (480, 270), interpolation=cv2.INTER_AREA))

    sp.ms_balanced = timed(balanced)
    sp.ms_light = timed(light)
    sp.ms_yuv = timed(yuv, 3)
    if models_dir is not None:
        try:
            from pathlib import Path

            from .detector import YuNetDetector
            det = YuNetDetector(Path(models_dir) / "face_detection_yunet_2023mar.onnx")
            sp.ms_detect = timed(lambda i=0: det.detect(frames[0]), 4)
        except Exception as e:
            sp.extra["detect_error"] = str(e)[:200]
    return sp


def choose_tier(sp: SystemSpec) -> int:
    """Starting tier for this PC. Conservative on the CPU side: the decoder
    (1080p60 H.264) and the UI need cores too."""
    if sp.cuda and sp.vram_gb >= 3.5 and sp.threads >= 4:
        return 0 if sp.gpu_sms >= 20 and sp.vram_gb >= 5.5 else 1
    thr = sp.threads
    # fraction of one 30 fps frame budget (33 ms) used by processing + detection share
    det_share = sp.ms_detect * 10 / 30            # ~10 Hz detection spread over 30 frames
    light = sp.ms_light + det_share
    # thresholds from live measurements on an emulated small PC (docs/TESTING.md):
    # 2 cores: Light 29 fps at 26 % CPU, Balanced only 23 fps; 1 core: Light 26 fps, Saver 29 fps
    if thr >= 8 and sp.ms_balanced + det_share <= 16:
        return 2
    if (thr >= 4 and light <= 14) or (thr >= 2 and light <= 8):
        return 3
    if (thr >= 2 and light <= 28) or light <= 8:
        return 4
    return 5


# Step-down paths. With a GPU, AI enhancement runs on the GPU and costs little CPU, so a busy
# CPU goes High -> Light (Balanced's fusion would move work onto the CPU). Measured on an
# emulated 2-core PC with a CPU hog: High -> Balanced dropped to 18 fps, Light held 28-30 fps.
GPU_PATH = [0, 1, 3, 4, 5]
SATURATION_FLOOR = 4          # CPU saturation alone (frames still on time) stops at Saver (720p)
CPU_PATH = [2, 3, 4, 5]


def path_for(sp: SystemSpec) -> list[int]:
    return GPU_PATH if sp.cuda else CPU_PATH


def ceiling_tier(sp: SystemSpec) -> int:
    """Highest tier the governor may climb to with sustained headroom."""
    return path_for(sp)[0]


@dataclass
class Metrics:
    """One sample of the live pipeline (taken every ~2 s)."""
    t: float
    expected_fps: float           # what the output should achieve (min of output rate, unique source rate)
    process_fps: float
    late_drops: int               # cumulative decoder late drops
    rebuffers: int                # cumulative
    cpu_pct: float                # CPU % of the cores FaceFrame may run on
    lat95_ms: float
    enhanced_requested: bool
    enhanced_running: bool
    active: bool                  # a panel is being processed (else only decode health counts)
    own_pct: float = 100.0        # FaceFrame's own share of those cores (0..100)


class Governor:
    """Steps the tier down quickly when the PC can't keep up and back up slowly."""

    def __init__(self, start: int, path: list[int]) -> None:
        self.path = list(path)
        # snap a start level that isn't on this path to the next lighter one
        self.tier = next((t for t in self.path if t >= start), self.path[-1])
        self._bad = 0
        self._good_since: float | None = None
        self._last_change = -1e9
        self._last_up = -1e9
        self._up_blocked_until = -1e9
        self._prev: Metrics | None = None
        self._cooldown = 6.0           # seconds after a change before judging again
        self._hot = 0                  # consecutive samples with the CPU (nearly) saturated
        self._good_cpu: list[float] = []
        self.last_reason = ""

    def reset(self, start: int, path: list[int]) -> None:
        self.__init__(start, path)

    def _step(self, d: int) -> int:
        i = self.path.index(self.tier) + d
        return self.path[max(0, min(len(self.path) - 1, i))]

    def update(self, m: Metrics) -> int | None:
        """Returns the new tier index when it changes, else None."""
        prev, self._prev = self._prev, m
        if prev is None:
            return None
        late = m.late_drops - prev.late_drops
        rebuf = m.rebuffers - prev.rebuffers
        slow_out = m.active and m.expected_fps > 5 and m.process_fps < 0.85 * m.expected_fps
        cpu_bound = m.cpu_pct >= 85
        decode_trouble = (late >= 3 or rebuf >= 1) and cpu_bound
        gpu_gave_up = m.enhanced_requested and not m.enhanced_running and m.active
        # a saturated CPU is a problem even when our frames still make it: the desktop gets
        # sluggish and TikTok LIVE Studio's encoder (same PC) has no room left
        # (only when FaceFrame itself is a large part of the load: lowering our level can't fix
        # another program's load, so that alone never goes below Saver)
        self._hot = self._hot + 1 if (m.cpu_pct >= 92 and m.own_pct >= 30) else 0
        overloaded = self._hot >= 3 and m.active and self.tier < SATURATION_FLOOR
        bad = slow_out or decode_trouble or gpu_gave_up or overloaded or (m.active and m.lat95_ms > 250)
        if bad:
            self._bad += 1
            self._good_since = None
            self._good_cpu = []
        else:
            self._bad = 0
            if self._good_since is None:
                self._good_since = m.t
            self._good_cpu.append(m.cpu_pct)
        since_change = m.t - self._last_change
        # step down: 2 bad samples in a row (~4 s), at most every 6 s; GPU fallback acts at once
        if (self._bad >= 2 or gpu_gave_up) and since_change >= self._cooldown and self.tier != self.path[-1]:
            if m.t - self._last_up < 60:
                self._up_blocked_until = m.t + 600     # the last step-up didn't hold: stay down 10 min
            old, self.tier = self.tier, self._step(+1)
            self._last_change = m.t
            self._cooldown = 20.0 if TIERS[old].max_height != TIERS[self.tier].max_height else 6.0
            self._bad = 0
            self._hot = 0
            self.last_reason = ("GPU enhancement could not keep up" if gpu_gave_up else
                                "the decoder fell behind" if decode_trouble else
                                "the processor is fully busy" if overloaded and not slow_out else
                                f"output {m.process_fps:.0f} of {m.expected_fps:.0f} fps" if slow_out else
                                f"latency {m.lat95_ms:.0f} ms")
            log.info("autotune: down to %s (%s)", TIERS[self.tier].name, self.last_reason)
            return self.tier
        # step up: 45 s healthy with clear headroom (average CPU of the whole window, not one sample)
        avg_cpu = sum(self._good_cpu) / len(self._good_cpu) if self._good_cpu else 100.0
        if (self.tier != self.path[0] and self._good_since is not None and m.t - self._good_since >= 45
                and since_change >= 45 and m.t >= self._up_blocked_until and avg_cpu < 60
                and m.lat95_ms < 120):
            old, self.tier = self.tier, self._step(-1)
            self._last_change = self._last_up = m.t
            self._cooldown = 20.0 if TIERS[old].max_height != TIERS[self.tier].max_height else 6.0
            self._good_since = m.t
            self._good_cpu = []
            self.last_reason = "headroom available"
            log.info("autotune: up to %s (headroom)", TIERS[self.tier].name)
            return self.tier
        return None


def spec_to_dict(sp: SystemSpec) -> dict:
    return asdict(sp)


def spec_from_dict(d: dict | None) -> SystemSpec | None:
    if not d:
        return None
    try:
        known = {k: d[k] for k in SystemSpec.__dataclass_fields__ if k in d}
        return SystemSpec(**known)
    except Exception:
        return None


def probe(models_dir=None, cached: dict | None = None, use_gpu: bool = True) -> SystemSpec:
    """Hardware + benchmark, reusing a cached benchmark when the hardware is unchanged."""
    hw = probe_hardware(use_gpu=use_gpu)
    old = spec_from_dict(cached)
    if old is not None and hw.same_hardware(old) and old.ms_light > 0:
        for k in ("ms_balanced", "ms_light", "ms_detect", "ms_yuv"):
            setattr(hw, k, getattr(old, k))
        hw.extra["bench_cached"] = True
        return hw
    return benchmark(hw, models_dir)


def disable_gpu_for_tests() -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
