# Third-party components

FaceFrame uses the following components. Their licences apply to those components.

| Component | Use | Licence |
|---|---|---|
| Real-ESRGAN models `realesr-general-x4v3`, `realesr-general-wdn-x4v3`, `RealESRGAN_x2plus` (Xintao Wang et al.) | GPU enhancement weights | BSD-3-Clause |
| YuNet face detector (`face_detection_yunet_2023mar.onnx`, OpenCV Zoo, Shiqi Yu) | face detection | MIT |
| Qt 6 / PySide6 | user interface | LGPL-3.0 |
| OpenCV | image processing | Apache-2.0 |
| PyTorch | GPU inference | BSD-3-Clause |
| FFmpeg (via PyAV) | stream demuxing and decoding | LGPL-2.1+ |
| PyAV | FFmpeg bindings | BSD-3-Clause |
| Streamlink | Twitch HLS access | BSD-2-Clause |
| windows-capture | Windows Graphics Capture | MIT |
| sounddevice / PortAudio | audio output | MIT |
| pycaw, comtypes | Volume Mixer session name | MIT |
| websockets | extension bridge | BSD-3-Clause |
| QtAwesome + Material Design Icons | UI icons | MIT / Apache-2.0 |
| NumPy, psutil, nvidia-ml-py | numerics, metrics | BSD-3-Clause |
| uv (Astral) | installer: private Python and packages | MIT / Apache-2.0 |
| Inno Setup | Windows installer | Inno Setup licence |

FaceFrame does not modify Edge, Twitch or TikTok LIVE Studio. Twitch content remains the property of its creators;
FaceFrame processes it locally on the user's PC.
