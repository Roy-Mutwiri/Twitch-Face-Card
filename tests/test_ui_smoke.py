"""Builds the real control window (offscreen) so wiring errors fail the test suite."""



def test_control_window_constructs_and_closes(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from faceframe import settings as sm
    from faceframe.ui import app as appmod
    monkeypatch.setattr(sm, "settings_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    s, p = sm.load(tmp_path / "s.json")
    s.bridge.port = 47897
    w = appmod.ControlWindow(s, p)
    w._best_preset()
    assert w.chk_temporal.isChecked() and w.s.quality.temporal and w.pipeline.cfg.temporal
    assert w.s.quality.mode == "enhanced"
    # clicked(bool) style call must not crash; the link dialog is answered "cancel" here
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    w.use_direct(False)
    w.close()


def test_playback_controls_with_direct_source(tmp_path, monkeypatch):
    """Pause/volume/time display wiring against a stub direct source (no network)."""
    from PySide6.QtWidgets import QApplication
    from faceframe import settings as sm
    from faceframe.capture.direct import DirectStreamSource
    from faceframe.ui import app as appmod
    monkeypatch.setattr(sm, "settings_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    s, p = sm.load(tmp_path / "s.json")
    s.bridge.port = 47896
    s.audio.enabled = False
    w = appmod.ControlWindow(s, p)
    src = DirectStreamSource("https://www.twitch.tv/videos/123")
    src._base = (100.0, __import__("time").perf_counter())
    src._first_pts, src.segment_start_s, src.duration_s = 100.0, 60.0, 3600.0
    w.pipeline.source = src
    w._update_playback()
    assert w.time_lbl.text().startswith("00:01:0") and "01:00:00" in w.time_lbl.text()
    assert w.remain_lbl.text().startswith("-00:5") and w.remain_lbl.text().endswith(" left")
    w._toggle_pause()
    w._update_playback()
    assert w.remain_lbl.text().endswith("· PAUSED")
    assert src.paused and w.btn_pause.accessibleName() == "Resume"
    w._toggle_pause()
    assert not src.paused
    w.btn_vol_down.click()
    assert abs(w.s.audio.volume - 0.9) < 1e-6 and w.vol_lbl.text() == "90 %"
    w.pipeline.source = None
    w.close()


def test_layout_fits_small_laptop_screens(tmp_path, monkeypatch):
    """Regression: on a 1500 px wide window the Output page was clipped (pages never scroll
    sideways). Every settings page must fit, and the window must fit a 1366×768 screen."""
    from PySide6.QtWidgets import QApplication
    from faceframe import settings as sm
    from faceframe.ui import app as appmod
    monkeypatch.setattr(sm, "settings_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    s, p = sm.load(tmp_path / "s.json")
    s.bridge.port = 47895
    w = appmod.ControlWindow(s, p)
    hint = w.minimumSizeHint()
    assert hint.width() <= 1340 and hint.height() <= 720, (hint.width(), hint.height())
    w.resize(hint.width(), 720)
    w.show()
    app.processEvents()
    for i in range(w.pages.count()):
        w.nav.setCurrentRow(i)
        app.processEvents()
        sc = w.pages.widget(i)
        need = sc.widget().minimumSizeHint().width()
        assert need <= sc.viewport().width(), (w.nav.item(i).text(), need, sc.viewport().width())
    w.close()
