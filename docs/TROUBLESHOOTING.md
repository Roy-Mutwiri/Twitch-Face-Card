# Troubleshooting

Logs: `%APPDATA%\FaceFrame\faceframe.log` · install log: `%LOCALAPPDATA%\Programs\FaceFrame\install.log` ·
self-test: `%APPDATA%\FaceFrame\selftest.json` (run `FaceFrame.exe --selftest`).

| Symptom | Cause / fix |
|---|---|
| SmartScreen "Windows protected your PC" | The installer is not code-signed. **More info → Run anyway.** |
| Installer says some components could not be installed | No internet during setup, or a download failed. Start menu → **Repair FaceFrame**. |
| "Enhanced unavailable: no compatible NVIDIA GPU" | No NVIDIA GPU or driver older than 528. FaceFrame uses Fast mode. Update the NVIDIA driver, then **Repair FaceFrame**. |
| "Enhanced too slow … switched to Fast" | The GPU is busy (games, encoders) or the output is very large. Use **1080 × 720** output, then choose Enhanced again. |
| Picture looks soft | Check the header: *×N from W×H*. Small webcams have few real pixels. Use the Twitch-link source, Best quality, 1080×720 output, **Head and shoulders** framing. |
| "Twitch stream unavailable … offline" | The channel is offline or the link is wrong. FaceFrame retries automatically. |
| Webcam area INVALID / neutral output | The stream layout changed or an ad is playing. It recovers by itself when the webcam returns; otherwise **Select webcam area** again. |
| Wrong person followed | **Select person** and click the right face; **Reset / reacquire** to start over. |
| Sound twice / echo | Mute the Edge tab, or turn off FaceFrame audio (Audio page). |
| No sound in LIVE Studio | Audio page → **CABLE Input**; LIVE Studio → audio input **CABLE Output** (VB-Audio Virtual Cable). |
| Lips and sound out of sync with *other* audio | Source page → **Video delay** (delays FaceFrame's picture and sound together). |
| LIVE Studio shows FaceFrame Output black | The output window is minimized. Restore it (it can be covered by other windows). |
| Edge window source shows STALLED | Edge stops drawing fully covered or minimized windows. Keep a sliver visible, or use the Twitch-link source. |
| FaceFrame doesn't open a second time | Only one FaceFrame runs at a time; the existing window is brought to the front. |
