"""Persisted settings (JSON). GUI-free. Invalid values are replaced by defaults
instead of crashing; the problems are reported back to the caller."""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger("faceframe.settings")

ASPECTS = {"16:9": 16 / 9, "3:2": 3 / 2, "4:3": 4 / 3, "1:1": 1.0, "4:5": 4 / 5, "9:16": 9 / 16}
OUTPUT_PRESETS = [(1280, 720), (1080, 720), (1920, 1080), (960, 540), (720, 720), (1080, 1080), (720, 1280)]


def settings_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "FaceFrame"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class FramingSettings:
    auto_frame: bool = True
    lock: bool = False
    framing: str = "head_shoulders"     # head_shoulders | wide | tight
    smoothness: float = 0.55
    fill: bool = True                   # True: fill crop at output aspect; False: fit with padding
    grace_s: float = 2.0
    after_grace: str = "panel"          # panel | neutral | hold
    detect_hz: float = 12.0
    unavailable_output: str = "neutral"  # neutral | hold


@dataclass
class QualityConfig:
    mode: str = "fast"                  # original | fast | enhanced
    sharpen: float = 0.25
    enhance_strength: float = 0.6
    sr_model: str = "realesr-general-x4v3"
    interpolation: str = "lanczos"
    sr_budget_ms: float = 45.0
    auto_fallback: bool = True
    temporal: bool = False              # multi-frame fusion (denoise using real neighbouring frames)
    temporal_strength: float = 0.75


@dataclass
class OutputConfig:
    width: int = 1280
    height: int = 720
    fps: int = 30
    aspect: str = "16:9"                # informational; width/height are authoritative
    frameless: bool = True
    x: int | None = None
    y: int | None = None


@dataclass
class SourceHint:
    kind: str = ""                      # wgc | native
    exe: str = ""
    title: str = ""
    hwnd: int = 0
    pid: int = 0
    # panel in normalized coordinates of the surface it was selected on
    panel_norm: list[float] | None = None
    panel_surface: list[int] | None = None
    panel_ref_scale: float = 0.0        # overview scale of the saved reference image
    direct_target: str = ""             # channel name or twitch.tv URL (direct stream source)
    direct_quality: str = "best"
    direct_delay: float = 0.0           # seconds of extra video delay (sync with audio)


@dataclass
class AudioConfig:
    enabled: bool = True                # play the direct stream's audio (FaceFrame in the Volume Mixer)
    device_name: str = ""               # "" = Windows default output
    volume: float = 1.0                 # 0..1.5
    muted: bool = False


@dataclass
class BridgeConfig:
    port: int = 47811
    token: str = ""
    allowed_extension_ids: list[str] = field(default_factory=list)


@dataclass
class AppSettings:
    framing: FramingSettings = field(default_factory=FramingSettings)
    quality: QualityConfig = field(default_factory=QualityConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    source: SourceHint = field(default_factory=SourceHint)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    models_dir: str = ""

    # ------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        problems: list[str] = []
        f, q, o = self.framing, self.quality, self.output

        def fix(cond: bool, msg: str, apply) -> None:
            if not cond:
                problems.append(msg)
                apply()

        fix(f.framing in ("head_shoulders", "wide", "tight"), f"framing {f.framing!r}",
            lambda: setattr(f, "framing", "head_shoulders"))
        fix(0.0 <= f.smoothness <= 1.0, "smoothness", lambda: setattr(f, "smoothness", 0.55))
        fix(0.0 <= f.grace_s <= 30.0, "grace_s", lambda: setattr(f, "grace_s", 2.0))
        fix(f.after_grace in ("panel", "neutral", "hold"), "after_grace", lambda: setattr(f, "after_grace", "panel"))
        fix(2.0 <= f.detect_hz <= 30.0, "detect_hz", lambda: setattr(f, "detect_hz", 12.0))
        fix(f.unavailable_output in ("neutral", "hold"), "unavailable_output",
            lambda: setattr(f, "unavailable_output", "neutral"))
        fix(q.mode in ("original", "fast", "enhanced"), f"quality mode {q.mode!r}", lambda: setattr(q, "mode", "fast"))
        fix(0.0 <= q.sharpen <= 1.0, "sharpen", lambda: setattr(q, "sharpen", 0.25))
        fix(0.0 <= q.enhance_strength <= 1.0, "enhance_strength", lambda: setattr(q, "enhance_strength", 0.6))
        fix(q.sr_model in ("realesr-general-x4v3", "RealESRGAN_x2plus"), "sr_model",
            lambda: setattr(q, "sr_model", "realesr-general-x4v3"))
        fix(q.interpolation in ("cubic", "lanczos"), "interpolation", lambda: setattr(q, "interpolation", "cubic"))
        fix(5.0 <= q.sr_budget_ms <= 500.0, "sr_budget_ms", lambda: setattr(q, "sr_budget_ms", 45.0))
        ok_size = (isinstance(o.width, int) and isinstance(o.height, int) and 64 <= o.width <= 3840
                   and 64 <= o.height <= 3840 and o.width % 2 == 0 and o.height % 2 == 0)
        fix(ok_size, f"output size {o.width}x{o.height} (even, 64..3840)",
            lambda: (setattr(o, "width", 1280), setattr(o, "height", 720)))
        fix(o.fps in (24, 25, 30, 50, 60), f"fps {o.fps}", lambda: setattr(o, "fps", 30))
        a = self.audio
        fix(isinstance(a.volume, (int, float)) and 0.0 <= a.volume <= 1.5, "audio volume",
            lambda: setattr(a, "volume", 1.0))
        b = self.bridge
        fix(isinstance(b.port, int) and 1024 <= b.port <= 65535, "bridge port", lambda: setattr(b, "port", 47811))
        if not b.token or len(b.token) < 32:
            b.token = secrets.token_urlsafe(32)
        if self.source.panel_norm is not None:
            pn = self.source.panel_norm
            ok = (isinstance(pn, list) and len(pn) == 4 and all(isinstance(v, (int, float)) for v in pn)
                  and pn[2] > 0 and pn[3] > 0 and pn[0] >= 0 and pn[1] >= 0
                  and pn[0] + pn[2] <= 1.0001 and pn[1] + pn[3] <= 1.0001)
            fix(ok, "stored panel bounds", lambda: setattr(self.source, "panel_norm", None))
        return problems

    # ------------------------------------------------------------------ I/O
    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "AppSettings":
        s = cls()
        for sub_name, sub_cls in (("framing", FramingSettings), ("quality", QualityConfig), ("output", OutputConfig),
                                  ("source", SourceHint), ("bridge", BridgeConfig), ("audio", AudioConfig)):
            raw = d.get(sub_name) or {}
            if not isinstance(raw, dict):
                continue
            target = getattr(s, sub_name)
            names = {f.name for f in fields(sub_cls)}
            for k, v in raw.items():
                if k in names:
                    setattr(target, k, v)
        if isinstance(d.get("models_dir"), str):
            s.models_dir = d["models_dir"]
        return s


def load(path: Path | None = None) -> tuple[AppSettings, list[str]]:
    path = path or settings_dir() / "settings.json"
    problems: list[str] = []
    s = AppSettings()
    if path.is_file():
        try:
            s = AppSettings.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))
        except Exception as e:
            problems.append(f"settings file unreadable ({e}); using defaults")
            s = AppSettings()
    problems += s.validate()
    return s, problems


def save(s: AppSettings, path: Path | None = None) -> None:
    path = path or settings_dir() / "settings.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(s.to_json(), encoding="utf-8")
    os.replace(tmp, path)
