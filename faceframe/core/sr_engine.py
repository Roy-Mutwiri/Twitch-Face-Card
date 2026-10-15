"""Persistent GPU super-resolution engine.

Weights are loaded once, kept warm on the GPU, and reused for every frame.
Nothing here spawns processes or reloads weights per frame. torch is imported
lazily so the non-AI path works without it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("faceframe.sr")


class SRError(RuntimeError):
    pass


@dataclass
class SRInfo:
    model: str
    scale: int
    device: str
    gpu_name: str
    precision: str
    fp16_psnr_db: float | None
    load_ms: float
    licence: str
    kind: str


_sha_cache: dict[str, str] = {}


def sha256_file(path: Path) -> str:
    key = f"{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return float("inf") if mse == 0 else 10.0 * np.log10(255.0 ** 2 / mse)


class SREngine:
    def __init__(self, model_name: str, models_dir: str | Path, prefer_fp16: bool = True,
                 denoise: float = 1.0) -> None:
        self.model_name = model_name
        # realesr-general-x4v3 only: 1.0 = model's normal (strong) denoise, 0.0 = the
        # "wdn" weak-denoise weights; in between = linear weight interpolation (Real-ESRGAN DNI)
        self.denoise = max(0.0, min(1.0, float(denoise)))
        self.models_dir = Path(models_dir)
        self.prefer_fp16 = prefer_fp16
        self.info: SRInfo | None = None
        self._model = None
        self._torch = None
        self._dtype = None
        self._lock = threading.Lock()
        self._channels_last = False
        self.autotune = False

    # ------------------------------------------------------------------ load
    def load(self) -> SRInfo:
        t0 = time.perf_counter()
        try:
            import torch  # noqa: WPS433 (lazy by design)
        except Exception as e:  # pragma: no cover - environment specific
            raise SRError(f"PyTorch not available: {e}") from e
        from .sr_models import MODEL_SPECS

        if self.model_name not in MODEL_SPECS:
            raise SRError(f"unknown SR model {self.model_name}")
        spec = MODEL_SPECS[self.model_name]
        path = self.models_dir / spec["file"]
        if not path.is_file():
            raise SRError(f"model weights missing: {path} (download from {spec['url']})")
        digest = sha256_file(path)
        if digest != spec["sha256"]:
            raise SRError(f"model checksum mismatch for {path.name}: {digest}")
        if not torch.cuda.is_available():
            raise SRError("CUDA GPU not available to PyTorch")
        dev = torch.device("cuda:0")
        torch.backends.cudnn.benchmark = False  # crop sizes vary frame to frame
        net = spec["build"]()
        state = torch.load(path, map_location="cpu", weights_only=True)
        if "params_ema" in state:
            state = state["params_ema"]
        elif "params" in state:
            state = state["params"]
        if self.model_name == "realesr-general-x4v3" and self.denoise < 0.999:
            from .sr_models import WDN_SPEC
            wpath = self.models_dir / WDN_SPEC["file"]
            if not wpath.is_file():
                raise SRError(f"denoise control needs {wpath.name} (download from {WDN_SPEC['url']})")
            if sha256_file(wpath) != WDN_SPEC["sha256"]:
                raise SRError(f"model checksum mismatch for {wpath.name}")
            wdn = torch.load(wpath, map_location="cpu", weights_only=True)
            wdn = wdn.get("params_ema", wdn.get("params", wdn))
            d = self.denoise
            state = {k: d * v + (1.0 - d) * wdn[k] for k, v in state.items()}
        net.load_state_dict(state, strict=True)
        net.eval().to(dev)
        self._torch = torch
        self._model = net
        self._dtype = torch.float32

        # FP16 correctness check against FP32 on a deterministic textured image.
        fp16_psnr = None
        precision = "fp32"
        test = _test_image(96, 128)
        ref = self._run_locked(test)
        if self.prefer_fp16:
            net.half()
            self._dtype = torch.float16
            try:
                half = self._run_locked(test)
                fp16_psnr = psnr(ref, half)
                if fp16_psnr >= 40.0 and np.isfinite(half).all():
                    precision = "fp16"
                else:
                    net.float()
                    self._dtype = torch.float32
            except Exception as e:  # pragma: no cover
                log.warning("fp16 check failed: %s", e)
                net.float()
                self._dtype = torch.float32
        if self._dtype == torch.float16:
            # channels-last FP16 uses the tensor cores better (measured 14.6 -> 11.6 ms on a 436x245 crop)
            net.to(memory_format=torch.channels_last)
            self._channels_last = True
        # warm-up for typical webcam crop sizes
        for hw in ((160, 128), (240, 200), (320, 256)):
            self._run_locked(_test_image(*hw))
        torch.cuda.synchronize()
        self.info = SRInfo(
            model=self.model_name, scale=spec["scale"], device=str(dev),
            gpu_name=torch.cuda.get_device_name(0), precision=precision,
            fp16_psnr_db=fp16_psnr, load_ms=(time.perf_counter() - t0) * 1000,
            licence=spec["licence"], kind=spec["kind"])
        log.info("SR engine ready: %s", self.info)
        return self.info

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def scale(self) -> int:
        return self.info.scale if self.info else 1

    # ------------------------------------------------------------------- run
    def run(self, bgr: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise SRError("SR engine not loaded")
        with self._lock:
            try:
                return self._run_locked(bgr)
            except self._torch.cuda.OutOfMemoryError as e:
                self._torch.cuda.empty_cache()
                raise SRError("GPU memory exhausted during enhancement") from e
            except RuntimeError as e:
                raise SRError(f"GPU runtime error: {e}") from e

    def _run_locked(self, bgr: np.ndarray) -> np.ndarray:
        torch = self._torch
        h, w = bgr.shape[:2]
        # pixel_unshuffle (x2 model) needs even dims: pad by replication, crop after.
        ph, pw = h % 2, w % 2
        src = bgr
        if ph or pw:
            src = np.pad(bgr, ((0, ph), (0, pw), (0, 0)), mode="edge")
        with torch.inference_mode():
            t = torch.from_numpy(np.ascontiguousarray(src[:, :, ::-1]))  # BGR->RGB
            t = t.to("cuda:0", non_blocking=True).permute(2, 0, 1).unsqueeze(0)
            t = t.to(self._dtype).div_(255.0)
            out = self._model(t)
            out = out.clamp_(0, 1).mul_(255.0).round_().to(torch.uint8)
            out = out.squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy()
        s = self.scale if self.info else (out.shape[0] // src.shape[0])
        out = out[: h * s, : w * s, ::-1]  # RGB->BGR, drop padding
        return np.ascontiguousarray(out)

    def run_resized(self, bgr: np.ndarray, out_w: int, out_h: int, strength: float = 1.0) -> np.ndarray:
        """SR + final size conversion + blend with a bicubic upscale, all on the GPU.
        Upload is the raw BGR crop (channel swap on the GPU); the crop is edge-padded to
        a multiple of 32 (padding, never resampling) so autotuned kernels are reused;
        only the final out_w x out_h image is copied back."""
        if self._model is None:
            raise SRError("SR engine not loaded")
        torch = self._torch
        F = torch.nn.functional
        with self._lock:
            try:
                h, w = bgr.shape[:2]
                H, W = -(-h // 32) * 32, -(-w // 32) * 32
                src = np.pad(bgr, ((0, H - h), (0, W - w), (0, 0)), mode="edge") if (H, W) != (h, w) else bgr
                # autotuning re-runs (up to ~200 ms) for every new input size; a zooming crop
                # produces many sizes, so it stays off (measured: p95 101 ms with it on)
                torch.backends.cudnn.benchmark = self.autotune
                with torch.inference_mode():
                    t = torch.from_numpy(np.ascontiguousarray(src)).to("cuda:0", non_blocking=True)
                    t = t.permute(2, 0, 1)[[2, 1, 0]].unsqueeze(0).to(self._dtype).div_(255.0)  # BGR->RGB on GPU
                    if self._channels_last:
                        t = t.contiguous(memory_format=torch.channels_last)
                    sr = self._model(t)
                    s = self.scale
                    sr = sr[:, :, : h * s, : w * s].float()
                    sr = F.interpolate(sr, size=(out_h, out_w), mode="bicubic", antialias=True, align_corners=False)
                    a = max(0.0, min(1.0, float(strength)))
                    if a < 0.999:
                        base = F.interpolate(t[:, :, :h, :w].float(), size=(out_h, out_w), mode="bicubic",
                                             align_corners=False)
                        sr = sr * a + base * (1.0 - a)
                    out = sr.clamp_(0, 1).mul_(255.0).round_().to(torch.uint8)[0, [2, 1, 0]]  # RGB->BGR on GPU
                    out = out.permute(1, 2, 0).contiguous().cpu().numpy()
                return out
            except self._torch.cuda.OutOfMemoryError as e:
                self._torch.cuda.empty_cache()
                raise SRError("GPU memory exhausted during enhancement") from e
            except RuntimeError as e:
                raise SRError(f"GPU runtime error: {e}") from e

    def memory_mb(self) -> tuple[float, float] | None:
        if self._torch is None:
            return None
        t = self._torch
        return t.cuda.memory_allocated() / 2 ** 20, t.cuda.memory_reserved() / 2 ** 20

    def close(self) -> None:
        with self._lock:
            self._model = None
            if self._torch is not None:
                self._torch.cuda.empty_cache()


def _test_image(h: int, w: int) -> np.ndarray:
    rng = np.random.default_rng(1234)
    yy, xx = np.mgrid[0:h, 0:w]
    base = (127 + 60 * np.sin(xx / 5.0) * np.cos(yy / 7.0)).astype(np.float32)
    img = np.stack([base, np.roll(base, 3, 1), np.roll(base, 5, 0)], -1)
    img += rng.normal(0, 6, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)
