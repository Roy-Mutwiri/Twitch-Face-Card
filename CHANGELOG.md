# Changelog

## 1.1.0 — 2026-10-15
**Highest quality & playback**
- Direct Twitch stream source (live and VODs, `?t=` start time): full 1080p60 decode via Streamlink + FFmpeg,
  low-latency, ads skipped, automatic reconnect, adaptive jitter buffer.
- Best quality preset: motion-compensated multi-frame fusion + GPU Real-ESRGAN (FP16, ~12 ms) + residual sharpening.
- In-sync stream audio (appears as "FaceFrame" in the Volume Mixer), output-device choice, volume, mute.
- Player bar: pause/resume (Space), position / length / remaining for VODs, LIVE time watched.

**Product**
- New interface: header status chips, icon navigation, card-based settings, player bar, app icon.
- Windows installer: per-user, bundled Python runtime, GPU-aware PyTorch install, self-test, Start-menu repair.
- Single-instance guard, crash reporting to the log, Documents\FaceFrame for user files.
- Extension pairing is automatic.

**Fixes found in release testing**
- Installer: junction creation blocked inside the installer process (bundled runtime instead); silent install could
  hang on an error dialog.
- Direct stream: live-edge stalls (switched HLS reader), clock jumps when no webcam area was selected, crash when
  stopping during decode.
- Auto-selection could pick a poster face while the streamer looked away (now requires a clearly dominant face).
- Settings file with a UTF-8 BOM reset the settings.
- Live QA (stylishnoob4): after a reconnect (network drop, long pause) the source kept reporting STALLED although
  video was playing again; resuming after a long pause could close the new live-edge connection. Both fixed;
  recovery now measured at 7.5–9.3 s.
- Audio: callback timing jitter caused audible resyncs (~1/min). Now a smoothed drift estimate with an inaudible
  ±0.4 % rate correction; hard resync only for real clock jumps.
- Header chip showed "SEARCHING" with no source loaded.

## 1.0.0 — 2026-10-15
- First version: Edge window capture, Edge extension bridge, face tracking and framing, Fast/Enhanced quality,
  clean output window for TikTok LIVE Studio.
