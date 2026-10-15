# FaceFrame user guide

FaceFrame takes the webcam corner of a Twitch stream, follows the streamer's face, sharpens it, and shows it in a
clean window called **FaceFrame Output** that you add to **TikTok LIVE Studio**. It can also play the stream's sound
in sync with the picture.

---

## 1. Install

1. Download **FaceFrame-Setup.exe** from the [latest release](https://github.com/Roy-Mutwiri/Twitch-Face-Card/releases/latest).
2. Run it. If Windows SmartScreen says *"Windows protected your PC"*, choose **More info → Run anyway**
   (the installer is not code-signed).
3. Click through the wizard. A setup window then downloads FaceFrame's components:
   - a private Python 3.12 (included in the installer) and the app's libraries (~150 MB download),
   - on PCs with an NVIDIA graphics card: the GPU AI runtime (~2.5 GB, a few minutes),
   - the AI models are verified and a self-test runs.
4. Start **FaceFrame** from the Start menu or the desktop.

No administrator rights are needed. Everything is installed in `%LOCALAPPDATA%\Programs\FaceFrame`.
Settings and the log live in `%APPDATA%\FaceFrame`.

| Your PC | What you get |
|---|---|
| NVIDIA GPU, driver 528 or newer | Everything, including the GPU **Enhanced** / **Best quality** mode |
| No NVIDIA GPU (or older driver) | Everything except AI enhancement — **Fast** mode, still the full stream quality |

Tick *"Skip the GPU AI runtime"* in the installer for a small install without AI enhancement.

---

## 2. Your first session (2 minutes)

1. **Source page → paste a Twitch link** (live channel such as `https://www.twitch.tv/stylishnoob4`, or a past
   broadcast `https://www.twitch.tv/videos/…`) and press **Enter**. Choose **best** quality.
2. After ~10 s the full 1080p picture appears on the left. Drag a rectangle **around the webcam corner**.
3. The streamer's face is picked automatically (green **following** box). If it picked the wrong face, press
   **Select person** and click the right one.
4. **Quality page → ★ Best quality preset.**
5. **Output page → Use 1080 × 720** (fits a 1080-wide portrait LIVE Studio canvas) → **Start output**.
6. In **TikTok LIVE Studio**: **Add source → Window capture → FaceFrame Output**. Leave *Capture cursor* off,
   place it at 100 % size, don't stretch it, and **Lock** it.

### Past broadcasts (VODs)
Twitch's **Share → Copy link at current time** gives a link like `…/videos/123?t=1h8m33s`; FaceFrame starts there.
The player bar shows `position / length` and the remaining time.

---

## 3. Sound

FaceFrame plays the stream's audio **in sync with the picture** and appears as **FaceFrame** in the Windows
Volume Mixer.

- **Player bar:** ⏯ pause/resume (or **Space**), volume −/+, slider (0–150 %), mute.
- **Audio page:** turn FaceFrame audio on/off and choose the **output device**.

**Send the sound into TikTok LIVE Studio** (needs the free [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)):
1. Audio page → output device **CABLE Input (VB-Audio Virtual Cable)**.
2. LIVE Studio → add **CABLE Output** as an audio input.
3. Mute the Edge tab (if you also watch there) so the stream isn't heard twice.

Windows only lists apps under *input* devices when they record a microphone; FaceFrame plays sound, so it appears
under *output*. The virtual cable is how its sound becomes an input for LIVE Studio.

---

## 4. Getting the best picture

The face can only be as detailed as the pixels Twitch sends. FaceFrame shows the real numbers in the header,
e.g. **ENHANCED + FUSION ×3.9 from 277×156** = the face crop has 277×156 real pixels, enlarged 3.9×.

| Do | Why |
|---|---|
| Use the **Twitch link** source (not window capture) | full 1080p decoded frames, independent of the browser |
| **Best quality preset** | multi-frame fusion removes stream compression noise; GPU AI upscaling sharpens |
| **Output 1080 × 720** for a portrait canvas | larger outputs only add enlargement and cost frame rate |
| **Framing → Head and shoulders** or **Wider** | uses more of the webcam's real pixels than *Tight* |
| Smaller streamer webcams | look softer — there is less detail in the stream |

Live and VOD of the same streamer give the same quality (measured: same 1080p60, ~9 Mbit/s).

---

## 5. Other capture methods

- **Edge window…** — captures an Edge window showing Twitch. Make the player large (the capture records what is
  drawn on screen) and don't minimize that window.
- **Edge extension** — reads the decoded video from the Twitch tab. Load `extension` from the install folder in
  `edge://extensions` (Developer mode → Load unpacked). Pairing is automatic.

The Twitch-link source is recommended: highest quality and independent of the browser.

---

## 6. Controls reference

| Where | Control | What it does |
|---|---|---|
| Header | status chips | source, tracking, webcam area, quality (with real crop size and enlargement), output, fps |
| Preview | Source / Webcam panel / Compare | full frame · webcam at 1:1 with boxes · same frame in two modes |
| Player bar | ⏯, time, volume, mute | playback and FaceFrame's own volume |
| Source | link box, Load stream, Video delay | open a stream; delay the picture+sound to line up with other audio |
| Source | Select webcam area / Select person / Reset | choose the webcam corner and the person |
| Framing | Auto frame, Lock frame, framing size, smoothness, after-lost behaviour | camera movement |
| Quality | Best quality preset, mode, fusion, sharpening, strength, AI model, resize filter | picture quality |
| Output | Start/Stop, size, frame rate, test pattern, sharpness advice | the window LIVE Studio captures |
| Diagnostics | live measurements | sizes, rates, latency, memory |

Keyboard: **Space** pause/resume · **Ctrl+1…7** settings pages · **Ctrl+M** hide controls (output keeps running).

---

## 7. Checking LIVE Studio isn't shrinking the picture

Output page → tick **Show test pattern** → record a few seconds locally in LIVE Studio → run
`tools\check_pattern.py <recording.mp4> --size 1080x720` from the install folder
(`.venv\Scripts\python tools\check_pattern.py …`). It reports *"1:1: no scaling detected"* or the exact scale.

---

## 8. Updating and uninstalling

- **Update:** run the newer FaceFrame-Setup.exe over the old one; settings are kept.
- **Repair:** Start menu → *Repair FaceFrame* (re-downloads components).
- **Uninstall:** Windows Settings → Apps → FaceFrame → Uninstall (settings in `%APPDATA%\FaceFrame` are kept).
