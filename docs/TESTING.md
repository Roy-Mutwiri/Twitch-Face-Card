# Testing & QA report: FaceFrame 1.3.0

Release testing on 2026-10-15 (the live QA ran on 1.1.0; 1.2.0 adds in-app updates, tested below). Everything below was **measured**. Failures are listed with their cause and fix,
not hidden.

**Test PC:** Windows 11 Pro 26200 · NVIDIA GeForce RTX 5060 Ti (driver ≥ 580) · Python 3.12 · live channel
**twitch.tv/stylishnoob4** (1080p60).

## Summary

| Area | Result |
|---|---|
| Automated tests (`pytest`) | **105 / 105 pass** (incl. updater tests) |
| Live QA suite, 10-min soak (`tools/qa_live.py`) | **18 / 19 pass**. The one miss happened while another app (Ollama) used 68 % of the GPU, see below |
| Fresh-PC install, NVIDIA (clean package cache) | ✅ exit 0, 7.6 min including the 2.5 GB GPU runtime, self-test OK (FP16 on GPU) |
| Fresh-PC install, CPU-only (no PyTorch) | ✅ exit 0, self-test OK, live stream runs in Fast mode at 29.7 fps |
| Release build: install → live run → uninstall | ✅ Best quality at 29.5 fps, SR 10.6 ms, p95 latency 35 ms; uninstall leaves nothing behind |
| Reconnect stress (3 long pauses + 3 dropped connections) | ✅ 6 / 6 back to the live 1080p60 picture in 7.5–9.3 s |
| Small screens (1366 × 768) | ✅ window min width 1274 px; every settings page fits (test enforced) |

## Bugs found and fixed in this QA round

| # | Found by | Bug | Fix | Proof |
|---|---|---|---|---|
| 1 | live soak | **Audio resyncs about once a minute**: callback timing jitter (GIL stalls > 60 ms) forced hard jumps, audible as clicks | Smoothed drift estimate plus an inaudible ±0.4 % rate correction; hard resync only for real clock jumps (> 250 ms) | 10 resyncs → **0**, audio served 99.1 % → **100 %**; `tests/test_audio_sync.py` |
| 2 | live QA | **After a reconnect the source stayed "STALLED"** even though video was playing (RUNNING was only reported on a size change) | RUNNING is reported on the first frame of every (re)connect | Stress test 0 / 6 → **6 / 6** recovered; regression test |
| 3 | live QA | **Resuming after a long live pause could close the new live-edge connection** (race in `resume()`) | Reconnect is flagged before unpausing; the decode thread swaps the connection itself | `pause_long_rejoins_live` FAIL → PASS (10.5 s) |
| 4 | live QA run 2 | **Crash in temporal fusion** when the webcam area was set while a frame was being fused (UI thread reset the history mid-process) | Fuser has its own lock | Regression test reproduces the exact live `TypeError` on the old code |
| 5 | screenshots | **Output page clipped on the right** at 1500 px window width (pages don't scroll sideways) | Narrower Output layout; the settings side can't be squeezed below its widest page; shrinkable player bar; the window opens to fit smaller screens | `test_layout_fits_small_laptop_screens` |
| 6 | review | **Test pattern crashed on a fresh PC** (`Documents\FaceFrame` missing → `mkdir` without parents) | `parents=True` | code review |
| 7 | review | Work files went to `%USERPROFILE%\Documents` even when Documents is redirected (OneDrive / another drive) | Uses the Windows known-folder API | manual check |
| 8 | review | Installer had no retry for a 2.5 GB download on a flaky connection | 300 s HTTP timeout, 3 attempts per step (resumes from cache) | retry logic tested with a failing command |
| 9 | install QA | Header chip said "SEARCHING" with no source loaded | Shows "NO TARGET" | screenshot |

Earlier in release testing: `os error 448` inside the installer (fixed by bundling the Python runtime), and silent
installs that could hang on an error dialog (fixed with `SuppressibleMsgBox`). See [CHANGELOG](../CHANGELOG.md).

## Live QA suite: final run (stylishnoob4, Best quality, 1080 × 720 output)

`python tools/qa_live.py --channel stylishnoob4 --soak-min 10` → `evidence/qa/qa_report.md`

| Check | Result | Measured |
|---|---|---|
| stream_acquired_best_rendition | ✅ | 1080p60, 1920 × 1080, first frame after 7.1 s |
| webcam_face_found / person_auto_selected | ✅ | webcam inset found and the streamer picked automatically |
| audio_device_opened | ✅ | WASAPI, 22 ms latency |
| soak_framerate | ❌ | median **29.7 fps** ✅, minimum 21.7 fps ❌ (see note) |
| soak_latency_p95_under_100ms | ✅ | median p95 43 ms, worst 87.8 ms |
| soak_no_quality_fallback | ✅ | Enhanced for the whole 10 minutes |
| soak_memory_stable | ✅ | RAM +9 MB over 10 min, VRAM constant (66 MB reserved) |
| soak_stream_stable | ✅ | 0 rebuffers, 0 late drops |
| soak_tracking | ✅ | tracking 100 %, webcam area valid 100 % |
| audio_continuous | ✅ | 100 % served, 0 resyncs, 0 underruns |
| threads_stable | ✅ | no thread growth |
| output_exact_size | ✅ | 1080 × 720 |
| highest_quality_chain_active | ✅ | Enhanced + fusion, face crop 321 × 214, ×3.36, SR 11.5 ms |
| pause_short_resume | ✅ | no new frames while paused; 29.7 fps after |
| pause_long_rejoins_live | ✅ | 10.5 s |
| network_drop_recovers | ✅ | 11.3 s |
| stop_start_cycles_no_leak | ✅ | 4 cycles: RAM 1177 → 1178 MB |
| offline_channel_handled | ✅ | reports STALLED and retries, never RUNNING |

**Note on `soak_framerate`:** between t = 43 s and 104 s the user's local Ollama `llama-server` took 68 % of the GPU.
FaceFrame's AI step rose from 12 ms to 25–30 ms and the frame rate dipped to 22–27 fps. It stayed in Enhanced
mode with tracking and audio intact, and returned to 30 fps once the load dropped. Without another GPU app, the
earlier run held 28–30 fps. **Running other heavy GPU apps at the same time lowers the frame rate.**

## Quality measurements

On a real 1080p60 Twitch frame sequence (`tools/quality_eval2.py`, face crop):

| Variant | x2 reconstruction PSNR / SSIM | Temporal noise (static background) | ms / frame |
|---|---|---|---|
| Fast (Lanczos + sharpen) | 34.14 dB / 0.937 | 0.741 | 16.7 |
| Fast + fusion | 33.89 dB / 0.932 | 0.678 | 31.1 |
| Enhanced x4v3 (strength 0.6) | 33.35 dB / **0.942** | 0.675 | 29.8 |
| Enhanced, full strength | 30.64 dB / 0.918 | 0.795 | 30.2 |
| **Best quality** (fusion + Enhanced) | 32.87 dB / 0.934 | **0.660** | 42.2* |

\*offline evaluation timing. In the live pipeline the stages overlap: SR about 11–12 ms and fusion about 5–6 ms,
for 30 fps output.

Live vs past broadcast of the same streamer: both are 1080p60 at about 9 Mbit/s (9.04 vs 8.44), with the same face
size, so the quality is the same. A soft picture comes from small webcams, Tight framing or very large outputs,
not from live mode.

## Small-PC performance and jump to time (1.3.0)

A small PC was **emulated** on the test machine: FaceFrame limited to 1–2 CPU cores, GPU hidden, live
stylishnoob4 (1080p60), 1080×720 output at 30 fps. The test machine's other programs kept those cores about 70 %
busy, so this is a *busy* small PC. These are emulations, not a real low-end laptop.

**Where the time went** (py-spy, 2 cores): the output resize (Lanczos) and full-size sharpening, two needless full-frame
copies, the YUV conversion's float pass, and face detection 12×/s. Fixed without quality loss (8-bit YUV
transform, max 1 level difference on 2.8 % of pixels; no copy when the crop fills the canvas). Light levels use
cheaper filters.

| Emulated PC | Level | Stream | Output fps (median / min) | CPU of its cores |
|---|---|---|---|---|
| 2 cores, before 1.3.0 | Fast | 1080p60 | 22.0 / 19.3 | 56 % |
| 2 cores | Balanced | 1080p60 | 23.1 / 17.4 | 54 % (too heavy → not chosen) |
| 2 cores | **Light** (auto-chosen) | 1080p60 | **29.2 / 28.0** | **26 %** |
| 2 cores | Saver | 720p60 | 29.7 / 28.4 | 13 % |
| 1 core | Light | 1080p60 | 25.6 / 22.2 | 43 % |
| 1 core | **Saver** (auto-chosen) | 720p60 | **29.1 / 26.9** | 20 % |
| 1 core | Minimal | 480p30 | 29.8 / 29.5 | 12 % |

**Governor in the real window** (2 cores, no GPU, live stream, CPU hog with 4 busy processes for 70 s):
Light was chosen and held **28–30 fps** before, during and after the hog, with 0 decoder drops. Other programs'
load did not lower the level. Two problems found by this test and fixed:
1. After the load ended it stepped up on one low CPU reading. Balanced then ran the 2 cores at 88–100 %.
   Step-up now needs the *average* CPU below 60 %, and sustained saturation caused by FaceFrame steps down.
2. Saturation caused by other programs pushed it down to 480p. Now it only counts when FaceFrame itself uses
   ≥ 30 % of the cores, and saturation alone stops at Saver.

With a GPU, Ultra held 29.8–29.9 fps while another process saturated the GPU with FP16 matrix multiplications
(AI step 12 → ~40 ms latency, still within budget). GPU PCs step Ultra → High → Light: Balanced's CPU fusion
would move work onto a busy CPU (measured 18 fps).

Seen once in 12 full-power runs: 0 fps while TRACKING for ~55 s without a step-down. It didn't reproduce in 11
more runs (including under GPU load). It is consistent with the webcam area's safety freeze after a stream layout
change. The governor now logs any such stall with the area state.

**Jump to time** (real window, VOD 18 h 21 min long): typed 1 h 2 m 3 s → Go → playing at 1:02:03. Seeks to 1:02:03,
10:00 and 1:23:20.5 landed within **0.4 s**, taking 10–15 s each (Twitch resolve + open). Seeking while paused also resumes.
Past-the-end times are refused, the controls are disabled for live links, and the player-bar button fills in the current time.

## In-app update (1.2.0)

Tested end to end with a local copy of the GitHub release feed. A fresh 1.2.0 install was marked as 1.1.9 and
started through `FaceFrame.exe`, like a user would:

| Step | Result |
|---|---|
| Start → pop-up "FaceFrame 1.2.0 is available" with release notes | ✅ shown after about 3 s |
| **Skip** | ✅ pop-up closes, app keeps running; log: "asks again next start" |
| Restart | ✅ pop-up shown again |
| **Update now** | ✅ download + SHA-256 check → app closes in 0.5 s → installer progress → **reopened as 1.2.0 in 37 s** |
| After the update | ✅ no pop-up (current = latest) |
| Bad checksum / offline / no newer release | ✅ unit tests: rejected / silently skipped / no pop-up |

Bugs found by this test and fixed: the update helper died when it started with `DETACHED_PROCESS` (PowerShell got
no console). The relaunched `FaceFrame.exe` failed with "Failed to load Python DLL" because PyInstaller variables
leaked through the launcher.

## Installer matrix

| Scenario | Result |
|---|---|
| Silent install, NVIDIA, empty download cache | exit 0 · 7.6 min · all 6 steps · self-test OK (`gpu_enhance` FP16) |
| Silent install, "Skip GPU runtime" task | exit 0 · no PyTorch installed · self-test OK (`gpu_enhance: unavailable`) · Fast mode end-to-end 29.7 fps |
| Release build `FaceFrame-Setup-1.1.0.exe` | exit 0 · live Best quality 29.5 fps · uninstall exit 0, folder removed |
| Uninstall | removes the folder, Start-menu entries, desktop shortcut and registry entry; keeps `%APPDATA%\FaceFrame` settings |
| Launcher, components missing | offers Repair (runs `bootstrap.ps1`) |
| Two copies | single-instance guard brings the existing window to the front |

## What is not verified

- Other GPUs (GTX / RTX 20–40 series, AMD, Intel): the driver → PyTorch mapping follows NVIDIA/PyTorch support
  tables, but only an RTX 5060 Ti was run. Non-NVIDIA PCs use Fast mode (verified with the CPU-only install).
- Windows 10: the installer requires 1903+. Only Windows 11 was run.
- A real TikTok LIVE Studio broadcast: FaceFrame never goes live. The output window, pixel-exact size and the
  test-pattern scaling check were verified locally.
- Twitch ads: Streamlink skips ad segments. During a mid-roll the picture holds and then continues. One gap of
  about 10 s in run 1 recovered automatically.

## Re-running the tests

```powershell
.venv\Scripts\python -m pytest -q                                   # 100 unit / integration tests
.venv\Scripts\python tools\qa_live.py --channel <live channel> --soak-min 10
.venv\Scripts\python tools\release_screens.py --channel <channel> --panel x,y,w,h
```
