import json
import os

import pytest

from faceframe import settings as sm


def test_invalid_settings_repaired(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "framing": {"framing": "giant", "smoothness": 7, "after_grace": "x"},
        "quality": {"mode": "magic", "sr_model": "RealESRGAN_x4plus_anime_6B"},
        "output": {"width": 1281, "height": 720, "fps": 144},
        "source": {"panel_norm": [0.9, 0.9, 0.5, 0.5]},
        "bridge": {"port": 80, "token": "short"},
    }))
    s, problems = sm.load(p)
    assert s.framing.framing == "head_shoulders" and s.framing.smoothness == 0.55
    assert s.quality.mode == "fast"
    assert s.quality.sr_model == "realesr-general-x4v3"   # anime model never accepted
    assert (s.output.width, s.output.height) == (1280, 720) and s.output.fps == 30
    assert s.source.panel_norm is None
    assert s.bridge.port == 47811 and len(s.bridge.token) >= 32
    assert len(problems) >= 7


def test_corrupt_file_falls_back(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    s, problems = sm.load(p)
    assert s.output.width == 1280 and problems


def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s, _ = sm.load(p)
    s.output.width, s.output.height = 1080, 720
    s.source.panel_norm = [0.1, 0.2, 0.3, 0.2]
    sm.save(s, p)
    s2, problems = sm.load(p)
    assert (s2.output.width, s2.output.height) == (1080, 720)
    assert s2.source.panel_norm == [0.1, 0.2, 0.3, 0.2]
    assert not problems


def test_wgc_refuses_missing_and_own_windows():
    from faceframe.capture.wgc import SourceRefused, validate_target
    with pytest.raises(SourceRefused):
        validate_target(0x7FFFFFF0)   # no such window
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setWindowTitle("FaceFrame Output")
    w.show()
    app.processEvents()
    with pytest.raises(SourceRefused, match="own window"):
        validate_target(int(w.winId()), allow_any_exe=True)
    w.close()


def test_wgc_binds_by_hwnd_only(monkeypatch):
    """The adapter must pass window_hwnd and never a monitor index or title."""
    import faceframe.capture.wgc as wgc
    calls = []

    class FakeCap:
        def __init__(self, **kw):
            calls.append(kw)

        def event(self, f):
            return f

        def start_free_threaded(self):
            class C:
                def stop(self):
                    pass

                def wait(self):
                    pass

                def is_finished(self):
                    return False
            return C()

    import windows_capture
    monkeypatch.setattr(windows_capture, "WindowsCapture", FakeCap)
    monkeypatch.setattr(wgc, "validate_target", lambda h, a=False: type("W", (), {
        "title": "t", "pid": 1, "exe": "msedge.exe", "dpi": 96, "rect": (0, 0, 10, 10)})())
    src = wgc.WgcSource(1234, 1)
    src._sink = None
    src._start_capture()
    assert calls and calls[0]["window_hwnd"] == 1234
    assert "monitor_index" not in calls[0] and "window_name" not in calls[0]
    assert calls[0]["cursor_capture"] is False and calls[0]["draw_border"] is False


def test_js_geometry_with_node():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(Path(__file__).parent / "js" / "geometry.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
