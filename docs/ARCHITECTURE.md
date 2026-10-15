# Architecture

```
            ┌──────────────────────── sources ────────────────────────┐
 Twitch ──► │ DirectStreamSource  Streamlink (HLS, low latency, ads    │
            │   skipped) → loopback TCP relay → FFmpeg/PyAV demux+decode│
            │   shared playback clock · audio decode (48 kHz)           │
 Edge   ──► │ WgcSource           Windows Graphics Capture, by HWND     │
 Edge tab ► │ BridgeSource        MV3 extension → WebSocket (loopback)  │
            └──────────┬──────────────────────────────────┬────────────┘
                 Frame (owned BGR panel, timestamps,       │ audio samples
                 geometry, clock domain)                   ▼
            ┌──────────▼──────────────────── Pipeline ──────────┐   AudioOutput
            │ LatestSlot queues (capacity 1, drop obsolete)     │   (WASAPI via
            │ detect thread  YuNet ~12 Hz → FaceTracker         │   sounddevice;
            │ validate thread  PanelValidator (scroll/resize/ad)│   asks the clock
            │ process thread  TemporalFuser → crop (smoother)   │   for samples due
            │                 → Renderer (Fast / Enhanced SR)   │   at DAC time)
            └──────────┬────────────────────────────────────────┘
                       ▼ OutputFrame (exact out_w × out_h)
            ┌──────────────────────┐        ┌─────────────────────────────┐
            │ OutputWindow         │ ◄────  │ ControlWindow (PySide6 UI)  │
            │ "FaceFrame Output"   │        │ header chips, preview, pages│
            └──────────────────────┘        └─────────────────────────────┘
```

## Packages

| Package | Contents |
|---|---|
| `faceframe/core` | GUI-free: frame contract, queues, geometry, detector, tracking + smoothing, panel validation, temporal fusion, renderer, SR engine, pipeline, metrics |
| `faceframe/capture` | `direct.py` (Twitch stream), `wgc.py` (window capture), `bridge.py` + `yuv.py` (extension), `audio.py`, `windows.py` |
| `faceframe/ui` | `app.py` control window, `theme.py` design system, `image_view.py`, `output_window.py`, `source_picker.py` |
| `extension/` | Edge MV3 extension (activeTab, scripting, storage; loopback only) |
| `installer/` | Inno Setup script, bootstrap, launcher, artwork, pinned requirements |
| `tools/` | validation and QA scripts (live QA, quality evaluation, scenario runners, probes) |
| `tests/` | 98 automated tests (contracts, geometry, tracking, protocol, UI wiring) |

## Key design decisions

- **Direct stream first.** Twitch's best rendition is decoded by FFmpeg, so quality does not depend on the
  browser player size. Streamlink's HLS reader (worker prefetch, low latency) is relayed to FFmpeg over a private
  loopback socket — FFmpeg's own HLS reader stalled at the live edge (measured).
- **One playback clock** drives the video schedule and the audio callback → lip sync, pause/resume, delay.
- **Latest-value queues everywhere.** Obsolete frames are dropped, never queued; detection results are rejected
  if they belong to an older frame/session.
- **Colour.** Decoder YUV planes are converted with the stream's matrix (BT.709 for HD) in one SIMD transform —
  verified against Edge's own conversion (mean difference 0.29 levels).
- **Quality chain.** Temporal fusion (motion-compensated, error-weighted, never smears motion) → Real-ESRGAN
  general x4v3 on the GPU (FP16, channels-last, resize+blend on the GPU) → thresholded residual sharpening when the
  enlargement exceeds the model's 4×. Every choice is backed by measurements in `docs/TESTING.md`.
- **Safety.** Window capture binds by HWND (never another window or the desktop); a person is never switched
  automatically to a different face; layout changes and ads produce a neutral frame, not a wrong face.
- **Installer.** Per-user Inno Setup; bundled Python runtime and `uv`; PyTorch CUDA build chosen from the NVIDIA
  driver version; self-test at the end.
