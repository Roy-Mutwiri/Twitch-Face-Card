"""FaceFrame control window and application entry point."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")  # physical-pixel UI: output size == configured px

from PySide6.QtCore import QSize, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QAction, QFont, QGuiApplication, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,  # noqa: E402
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QRadioButton, QScrollArea,
                               QSlider, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .. import __version__, settings as settings_mod  # noqa: E402
from ..capture import windows as win  # noqa: E402
from ..capture.audio import AudioOutput, output_devices  # noqa: E402
from ..capture.bridge import BridgeServer, BridgeSource  # noqa: E402
from ..capture.direct import DirectStreamSource, list_qualities, normalize_target, parse_hms  # noqa: E402
from ..capture.wgc import SourceRefused, WgcSource  # noqa: E402
from ..core.enhance import QualityMode, QualitySettings  # noqa: E402
from ..core.geometry import Rect, from_normalized, to_normalized  # noqa: E402
from ..core.panel_validate import PanelValidity  # noqa: E402
from ..core.pipeline import Pipeline, PipelineConfig  # noqa: E402
from ..core.source_base import SourceState  # noqa: E402
from ..core.tracking import TrackState  # noqa: E402
from . import theme as T  # noqa: E402
from .image_view import ImageView, Overlay  # noqa: E402
from .output_window import OUTPUT_TITLE, OutputWindow  # noqa: E402
from .source_picker import SourcePickerDialog  # noqa: E402
from .perf_ui import PerformanceController  # noqa: E402
from .update_ui import UpdateController  # noqa: E402

log = logging.getLogger("faceframe.app")

def _documents_dir() -> Path:
    """The user's real Documents folder (may be redirected, e.g. to OneDrive or another drive)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Documents"


APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
# writable location for evidence/compare PNGs (next to the exe when frozen)
if getattr(sys, "frozen", False):
    WORK_ROOT = Path(sys.executable).parent
elif (APP_ROOT / "installed.flag").exists():
    WORK_ROOT = _documents_dir() / "FaceFrame"
else:
    WORK_ROOT = APP_ROOT


def models_dir(s: settings_mod.AppSettings) -> Path:
    if s.models_dir and Path(s.models_dir).is_dir():
        return Path(s.models_dir)
    for cand in (APP_ROOT / "models", Path(sys.executable).parent / "models"):
        if cand.is_dir():
            return cand
    return APP_ROOT / "models"


def fmt(v, spec=".1f", none="–"):
    try:
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return none
        return format(v, spec)
    except Exception:
        return str(v)


class ControlWindow(QMainWindow):
    engineLoaded = Signal(object, str)

    def __init__(self, s: settings_mod.AppSettings, problems: list[str]) -> None:
        super().__init__()
        self.s = s
        self.setWindowTitle(f"FaceFrame {__version__} — Controls")
        self.setWindowIcon(T.app_icon())
        # fit smaller screens (e.g. 1366×768 laptops): never open larger than the work area
        scr = QApplication.primaryScreen()
        ag = scr.availableGeometry() if scr is not None else None
        if ag is not None and (ag.width() < 1700 or ag.height() < 1000):
            self.resize(min(1640, ag.width() - 20), min(960, ag.height() - 40))
            self.move(ag.x() + 10, ag.y() + 10)
        else:
            self.resize(1640, 960)
        self.mdir = models_dir(s)
        self.pipeline = Pipeline(self.mdir, self._pipeline_config())
        self.engine = None
        self._engine_loading = False
        self.bridge: BridgeServer | None = None
        self._start_bridge()
        self.output = OutputWindow(self.pipeline, s.output.width, s.output.height, s.output.frameless)
        self.output_running = False
        self.audio = AudioOutput(self)           # provider = self.read_audio (current direct source)
        self._restore_pending: dict | None = None
        self._compare_frozen = None
        self._auto_offer_done = False
        self.engineLoaded.connect(self._on_engine_loaded)
        self._applying_tier = False
        self.perf = PerformanceController(self)
        self._build_ui()
        self._load_into_ui()
        if problems:
            self.notice(f"Settings repaired: {', '.join(problems)}", warn=True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)   # ~30 Hz previews
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_settings)
        self._offer_resume()
        self.perf.start()               # probe this PC (cached), pick the quality level, then adapt live
        # updates: checked in the background a few seconds after start; "Skip" asks again next start
        self.updates = UpdateController(self)
        if UpdateController.enabled():
            QTimer.singleShot(3000, self.updates.check)
        if s.quality.mode == "enhanced":
            self._ensure_engine()

    # ================================================================== setup
    def _pipeline_config(self) -> PipelineConfig:
        s = self.s
        q = s.quality
        return PipelineConfig(
            out_w=s.output.width, out_h=s.output.height, out_fps=s.output.fps, framing=s.framing.framing,
            fill=s.framing.fill, auto_frame=s.framing.auto_frame, lock=s.framing.lock,
            smoothness=s.framing.smoothness, detect_hz=s.framing.detect_hz,
            unavailable_output=s.framing.unavailable_output,
            temporal=s.quality.temporal, temporal_strength=s.quality.temporal_strength,
            quality=QualitySettings(mode=QualityMode(q.mode), sharpen=q.sharpen, enhance_strength=q.enhance_strength,
                                    sr_model=q.sr_model, interpolation=q.interpolation, sr_budget_ms=q.sr_budget_ms,
                                    auto_fallback=q.auto_fallback))

    def _start_bridge(self) -> None:
        b = self.s.bridge
        self.bridge = BridgeServer(b.port, b.token, b.allowed_extension_ids or None)
        self.bridge.start()
        if self.bridge.error:
            log.warning(self.bridge.error)

    # ===================================================================== UI
    @staticmethod
    def _card(title: str, hint: str = "", icon_name: str | None = None):
        """A titled card: returns (frame, body layout)."""
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(T.icon(icon_name, T.ACCENT_HI).pixmap(18, 18))
            head.addWidget(ic)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        head.addWidget(t)
        head.addStretch(1)
        v.addLayout(head)
        if hint:
            h = QLabel(hint)
            h.setObjectName("cardHint")
            h.setWordWrap(True)
            v.addWidget(h)
        body = QVBoxLayout()
        body.setSpacing(8)
        v.addLayout(body)
        return f, body

    @staticmethod
    def _btn(text: str, icon_name: str | None = None, kind: str | None = None, color: str = T.TEXT) -> QPushButton:
        b = QPushButton(text)
        if icon_name:
            b.setIcon(T.icon(icon_name, "white" if kind in ("primary", "hero", "round") else color))
            b.setIconSize(QSize(18, 18))
        if kind:
            b.setProperty("kind", kind)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _page(self, title: str, sub: str):
        """Scrollable settings page: returns (scroll area, content layout)."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 10, 10)
        v.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        s = QLabel(sub)
        s.setObjectName("pageSub")
        s.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(s)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(w)
        return sc, v

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------------------------------------------------- header bar
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(T.app_icon_pixmap(34))
        hl.addWidget(logo)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        title = QLabel("FaceFrame")
        title.setObjectName("appTitle")
        subtitle = QLabel("Automatic webcam framing for TikTok LIVE Studio")
        subtitle.setObjectName("appSubtitle")
        tl.addWidget(title)
        tl.addWidget(subtitle)
        hl.addLayout(tl)
        hl.addStretch(1)
        self.chips = {}
        for key, ic_name in (("source", "access-point"), ("track", "face-recognition"), ("area", "crop-free"),
                             ("quality", "creation"), ("output", "cast"), ("fps", "speedometer")):
            c = QLabel("")
            c.setObjectName("chip")
            c.setProperty("state", "idle")
            c.setProperty("iconName", ic_name)
            hl.addWidget(c)
            self.chips[key] = c
        root.addWidget(header)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setContentsMargins(12, 12, 12, 12)
        bw = QWidget()
        bl = QVBoxLayout(bw)
        bl.setContentsMargins(12, 12, 12, 12)
        bl.addWidget(body)
        root.addWidget(bw, 1)

        # ------------------------------------------------------- preview card
        prev = QFrame()
        prev.setObjectName("previewCard")
        pv = QVBoxLayout(prev)
        pv.setContentsMargins(12, 10, 12, 12)
        pv.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.view_source = ImageView(placeholder="No source yet.\n\nPaste a Twitch link on the Source page "
                                                 "(highest quality), or pick an Edge window.")
        self.view_panel = ImageView(placeholder="Select the webcam area first.")
        self.view_source.rectSelected.connect(self._on_panel_rect)
        self.view_panel.clicked.connect(self._on_person_click)
        cmp = QWidget()
        cl = QVBoxLayout(cmp)
        cl.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        lab = QLabel("Compare live output with")
        lab.setObjectName("muted")
        row.addWidget(lab)
        self.cmp_mode = QComboBox()
        self.cmp_mode.addItems(["enhanced", "fast", "original"])
        self.cmp_mode.currentTextChanged.connect(self._on_compare_mode)
        row.addWidget(self.cmp_mode)
        self.cmp_freeze = QCheckBox("Freeze frame")
        row.addWidget(self.cmp_freeze)
        b = self._btn("Save PNGs", "content-save-outline")
        b.clicked.connect(self._save_compare)
        row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        pair = QHBoxLayout()
        self.view_cmp_a = ImageView(placeholder="Live output")
        self.view_cmp_b = ImageView(placeholder="Comparison mode")
        self.view_cmp_a.smooth = self.view_cmp_b.smooth = True
        pair.addWidget(self.view_cmp_a)
        pair.addWidget(self.view_cmp_b)
        cl.addLayout(pair, 1)
        self.cmp_note = QLabel("Same source frame, same output size, two modes. Inspect at 1:1 in the output "
                               "window or the saved PNGs.")
        self.cmp_note.setObjectName("muted")
        self.cmp_note.setWordWrap(True)
        cl.addWidget(self.cmp_note)
        self.tabs.addTab(self.view_source, T.icon("monitor-screenshot", T.MUTED), "Source")
        self.tabs.addTab(self.view_panel, T.icon("webcam", T.MUTED), "Webcam panel")
        self.tabs.addTab(cmp, T.icon("compare-horizontal", T.MUTED), "Compare")
        self.tabs.currentChanged.connect(self._on_tab)
        pv.addWidget(self.tabs, 1)

        # transport bar (media-player style)
        tr = QFrame()
        tr.setObjectName("transport")
        tl2 = QHBoxLayout(tr)
        tl2.setContentsMargins(12, 8, 14, 8)
        tl2.setSpacing(10)
        self.btn_pause = self._btn("", "pause", "round")
        self.btn_pause.setIconSize(QSize(24, 24))
        self.btn_pause.setToolTip("Pause / resume (Space). Live streams jump back to live if paused longer than 8 s.")
        self.btn_pause.clicked.connect(self._toggle_pause)
        tl2.addWidget(self.btn_pause)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        self.time_lbl = QLabel("--:--:-- / --:--:--")
        self.time_lbl.setObjectName("timeBig")
        self.time_lbl.setToolTip("Position / total length (VOD). Live: time watched.")
        self.remain_lbl = QLabel("")
        self.remain_lbl.setObjectName("timeSmall")
        tcol.addWidget(self.time_lbl)
        tcol.addWidget(self.remain_lbl)
        tl2.addLayout(tcol)
        self.btn_goto = self._btn("", "clock-fast", "ghost")
        self.btn_goto.setToolTip("Jump to a time (past broadcasts)")
        self.btn_goto.clicked.connect(self._focus_jump)
        tl2.addWidget(self.btn_goto)
        tl2.addStretch(1)
        self.chk_mute = self._btn("", "volume-high", "ghost")
        self.chk_mute.setCheckable(True)
        self.chk_mute.setToolTip("Mute FaceFrame audio")
        self.chk_mute.toggled.connect(lambda on: self.chk_mute.setIcon(
            T.icon("volume-off" if on else "volume-high", T.ERR if on else T.TEXT)))
        self.btn_vol_down = self._btn("", "minus", "ghost")
        self.btn_vol_up = self._btn("", "plus", "ghost")
        self.btn_vol_down.setToolTip("Volume −10 %")
        self.btn_vol_up.setToolTip("Volume +10 %")
        self.sld_vol = QSlider(Qt.Orientation.Horizontal)
        self.sld_vol.setRange(0, 150)
        self.sld_vol.setMinimumWidth(70)
        self.sld_vol.setMaximumWidth(150)
        self.vol_lbl = QLabel("100 %")
        self.vol_lbl.setObjectName("timeSmall")
        self.vol_lbl.setMinimumWidth(46)
        self.btn_vol_down.clicked.connect(lambda: self.sld_vol.setValue(self.sld_vol.value() - 10))
        self.btn_vol_up.clicked.connect(lambda: self.sld_vol.setValue(self.sld_vol.value() + 10))
        for wdg in (self.chk_mute, self.btn_vol_down, self.sld_vol, self.btn_vol_up, self.vol_lbl):
            tl2.addWidget(wdg)
        pv.addWidget(tr)

        # notice banner
        self.notice_frame = QFrame()
        self.notice_frame.setObjectName("notice")
        nl = QHBoxLayout(self.notice_frame)
        nl.setContentsMargins(12, 8, 12, 8)
        self.notice_icon = QLabel()
        self.notice_icon.setPixmap(T.icon("information-outline", "#9cc0ff").pixmap(18, 18))
        self.notice_lbl = QLabel("")
        self.notice_lbl.setObjectName("noticeText")
        self.notice_lbl.setWordWrap(True)
        nl.addWidget(self.notice_icon, 0, Qt.AlignmentFlag.AlignTop)
        nl.addWidget(self.notice_lbl, 1)
        self.notice_frame.setVisible(False)
        pv.addWidget(self.notice_frame)
        body.addWidget(prev)

        # ------------------------------------------------- settings: nav + pages
        side = QFrame()
        side.setObjectName("sideCard")
        sl = QHBoxLayout(side)
        sl.setContentsMargins(6, 8, 8, 8)
        sl.setSpacing(6)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(168)
        self.nav.setIconSize(QSize(20, 20))
        self.pages = QStackedWidget()
        sl.addWidget(self.nav)
        sl.addWidget(self.pages, 1)
        body.addWidget(side)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setSizes([980, 640])

        def add_page(name, ic_name, page):
            it = QListWidgetItem(T.icon(ic_name, T.MUTED, active=T.TEXT), name)
            it.setSizeHint(QSize(150, 42))
            self.nav.addItem(it)
            self.pages.addWidget(page)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        # ---- Source page
        page, v = self._page("Source", "Where the picture comes from, and which webcam/person to follow.")
        card, cb = self._card("Twitch stream — highest quality", "FaceFrame downloads the stream itself at the best "
                              "rendition (e.g. 1080p60) and decodes every full frame. Works for live channels and "
                              "VODs (Share → copy link at the current time).", "broadcast")
        self.edit_direct = QLineEdit()
        self.edit_direct.setObjectName("bigInput")
        self.edit_direct.setPlaceholderText("Paste a twitch.tv link or channel name, then press Enter")
        self.edit_direct.setClearButtonEnabled(True)
        self.edit_direct.addAction(T.icon("link-variant", T.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.edit_direct.returnPressed.connect(lambda: self.use_direct(self.edit_direct.text().strip() or None))
        cb.addWidget(self.edit_direct)
        self.btn_direct = self._btn("Load stream", "play-circle-outline", "primary")
        self.btn_direct.setToolTip("Decode the Twitch stream directly at full resolution")
        self.btn_direct.clicked.connect(lambda: self.use_direct(self.edit_direct.text().strip() or None))
        r = QHBoxLayout()
        r.addWidget(self.btn_direct, 1)
        cb.addLayout(r)
        r = QHBoxLayout()
        dl = QLabel("Video delay")
        dl.setObjectName("muted")
        r.addWidget(dl)
        self.spn_delay = QDoubleSpinBox()
        self.spn_delay.setRange(0.0, 30.0)
        self.spn_delay.setSingleStep(0.1)
        self.spn_delay.setDecimals(1)
        self.spn_delay.setSuffix(" s")
        self.spn_delay.setToolTip("Hold the picture (and FaceFrame's audio) back to line up with other audio")
        self.spn_delay.valueChanged.connect(self._on_delay)
        r.addWidget(self.spn_delay)
        r.addStretch(1)
        cb.addLayout(r)
        v.addWidget(card)

        card, cb = self._card("Jump to time", "Past broadcasts (VOD links): type the hour, minute and second, "
                              "then press Go or Enter.", "clock-fast")
        self.jump_card = card
        r = QHBoxLayout()
        self.spn_jh, self.spn_jm, self.spn_js = QSpinBox(), QSpinBox(), QSpinBox()
        for sp, mx, suf in ((self.spn_jh, 99, " h"), (self.spn_jm, 59, " m"), (self.spn_js, 59, " s")):
            sp.setRange(0, mx)
            sp.setSuffix(suf)
            sp.setMinimumWidth(64)
            sp.lineEdit().returnPressed.connect(self._jump)
            r.addWidget(sp, 1)
        self.btn_jump = self._btn("Go", "skip-next-circle-outline", "primary")
        self.btn_jump.clicked.connect(self._jump)
        r.addWidget(self.btn_jump)
        cb.addLayout(r)
        self.jump_hint = QLabel("Load a past broadcast (twitch.tv/videos/…) to use this.")
        self.jump_hint.setObjectName("cardHint")
        self.jump_hint.setWordWrap(True)
        cb.addWidget(self.jump_hint)
        v.addWidget(card)

        card, cb = self._card("Other capture methods", "Use these if you prefer to capture the Edge player itself.",
                              "monitor-multiple")
        r = QHBoxLayout()
        self.btn_source = self._btn("Edge window…", "monitor-screenshot")
        self.btn_source.setToolTip("Capture a Microsoft Edge window (rendered pixels)")
        self.btn_source.clicked.connect(self.select_source)
        self.btn_native = self._btn("Edge extension", "puzzle-outline")
        self.btn_native.setToolTip("Decoded pixels from the Edge tab via the FaceFrame Bridge extension")
        self.btn_native.clicked.connect(self.use_native)
        r.addWidget(self.btn_source)
        r.addWidget(self.btn_native)
        cb.addLayout(r)
        v.addWidget(card)

        card, cb = self._card("Session", "", "pulse")
        self.src_status = QLabel("No source")
        self.src_status.setWordWrap(True)
        cb.addWidget(self.src_status)
        self.btn_resume = self._btn("Resume previous source", "history")
        self.btn_resume.clicked.connect(self.resume_source)
        self.btn_resume.setVisible(False)
        cb.addWidget(self.btn_resume)
        r = QHBoxLayout()
        self.btn_stop_src = self._btn("Stop source", "stop-circle-outline", "danger", "#fca5a5")
        self.btn_stop_src.clicked.connect(self.stop_source)
        self.btn_recover = self._btn("", "restore")
        self.btn_recover.setVisible(False)
        self.btn_recover.clicked.connect(self._recover_action)
        r.addWidget(self.btn_stop_src)
        r.addWidget(self.btn_recover)
        r.addStretch(1)
        cb.addLayout(r)
        v.addWidget(card)

        card, cb = self._card("Webcam & person", "Drag around the webcam inset in the preview; the main face is "
                              "picked automatically, or click the person yourself.", "account-box-outline")
        g = QGridLayout()
        self.btn_area = self._btn("Select webcam area", "crop-free")
        self.btn_area.setCheckable(True)
        self.btn_area.toggled.connect(self._toggle_area_select)
        self.btn_area_page = self._btn("Select in page", "cursor-default-click-outline")
        self.btn_area_page.setToolTip("Extension mode: drag the rectangle directly on the Twitch player")
        self.btn_area_page.clicked.connect(self._select_area_in_page)
        self.btn_person = self._btn("Select person", "account-search-outline")
        self.btn_person.setCheckable(True)
        self.btn_person.toggled.connect(self._toggle_person_select)
        self.btn_reset = self._btn("Reset / reacquire", "refresh")
        self.btn_reset.clicked.connect(self._reset_tracking)
        self.btn_confirm = self._btn("Confirm restored webcam area", "check-decagram-outline", "primary")
        self.btn_confirm.setVisible(False)
        self.btn_confirm.clicked.connect(self._confirm_restored)
        g.addWidget(self.btn_area, 0, 0)
        g.addWidget(self.btn_area_page, 0, 1)
        g.addWidget(self.btn_person, 1, 0)
        g.addWidget(self.btn_reset, 1, 1)
        g.addWidget(self.btn_confirm, 2, 0, 1, 2)
        cb.addLayout(g)
        v.addWidget(card)
        v.addStretch(1)
        add_page("Source", "access-point", page)

        # ---- Audio page
        page, v = self._page("Audio", "FaceFrame plays the stream's sound in sync with the picture. It appears as "
                                      "“FaceFrame” in the Windows Volume Mixer.")
        card, cb = self._card("Playback audio", "", "speaker")
        self.chk_audio = QCheckBox("Play stream audio from FaceFrame (in sync)")
        self.chk_audio.setToolTip("Turn off if you use the Edge tab's sound instead.")
        cb.addWidget(self.chk_audio)
        r = QHBoxLayout()
        ol = QLabel("Output device")
        ol.setObjectName("muted")
        r.addWidget(ol)
        self.cmb_audio_dev = QComboBox()
        r.addWidget(self.cmb_audio_dev, 1)
        self.btn_audio_refresh = self._btn("", "refresh", "ghost")
        self.btn_audio_refresh.setToolTip("Refresh the device list")
        self.btn_audio_refresh.clicked.connect(self._fill_audio_devices)
        r.addWidget(self.btn_audio_refresh)
        cb.addLayout(r)
        self.audio_status = QLabel("")
        self.audio_status.setObjectName("muted")
        self.audio_status.setWordWrap(True)
        cb.addWidget(self.audio_status)
        v.addWidget(card)
        card, cb = self._card("Send the sound into TikTok LIVE Studio", "", "transit-connection-variant")
        self.audio_hint = QLabel("1. Choose <b>CABLE Input (VB-Audio Virtual Cable)</b> as the output device above."
                                 "<br>2. In LIVE Studio add <b>CABLE Output</b> as an audio input."
                                 "<br>3. Mute the Edge tab so the stream isn't heard twice.")
        self.audio_hint.setWordWrap(True)
        cb.addWidget(self.audio_hint)
        v.addWidget(card)
        v.addStretch(1)
        add_page("Audio", "volume-high", page)

        # ---- Framing page
        page, v = self._page("Framing", "How the camera follows the selected person.")
        card, cb = self._card("Auto framing", "", "image-filter-center-focus")
        fl = QFormLayout()
        fl.setHorizontalSpacing(14)
        fl.setVerticalSpacing(10)
        self.chk_auto = QCheckBox("Auto frame")
        self.chk_lock = QCheckBox("Lock frame")
        r = QHBoxLayout()
        r.addWidget(self.chk_auto)
        r.addWidget(self.chk_lock)
        r.addStretch(1)
        fl.addRow(r)
        self.cmb_framing = QComboBox()
        self.cmb_framing.addItem(T.icon("account", T.MUTED), "Head and shoulders", "head_shoulders")
        self.cmb_framing.addItem(T.icon("account-multiple-outline", T.MUTED), "Wider", "wide")
        self.cmb_framing.addItem(T.icon("face-man-outline", T.MUTED), "Tight (face)", "tight")
        fl.addRow("Framing", self.cmb_framing)
        self.cmb_fill = QComboBox()
        self.cmb_fill.addItem("Fill (crop to output aspect)", True)
        self.cmb_fill.addItem("Fit (pad, never crop more)", False)
        fl.addRow("Aspect", self.cmb_fill)
        self.sld_smooth = QSlider(Qt.Orientation.Horizontal)
        self.sld_smooth.setRange(0, 100)
        fl.addRow("Smoothness", self.sld_smooth)
        cb.addLayout(fl)
        v.addWidget(card)
        card, cb = self._card("When the person is lost", "", "account-question-outline")
        fl = QFormLayout()
        fl.setHorizontalSpacing(14)
        fl.setVerticalSpacing(10)
        self.cmb_lost = QComboBox()
        self.cmb_lost.addItem("Show whole webcam panel", "panel")
        self.cmb_lost.addItem("Neutral (blank) output", "neutral")
        self.cmb_lost.addItem("Keep last framing", "hold")
        fl.addRow("Then", self.cmb_lost)
        self.spn_grace = QDoubleSpinBox()
        self.spn_grace.setRange(0.0, 30.0)
        self.spn_grace.setSingleStep(0.5)
        self.spn_grace.setSuffix(" s")
        fl.addRow("Hold framing for", self.spn_grace)
        cb.addLayout(fl)
        v.addWidget(card)
        v.addStretch(1)
        add_page("Framing", "image-filter-center-focus", page)

        # ---- Quality page
        page, v = self._page("Quality", "Twitch's pixels can't be exceeded — this gets the most out of them.")
        v.addWidget(self.perf.build_card())
        self.btn_best = self._btn("  Best quality preset", "star-four-points", "hero")
        self.btn_best.setToolTip("Multi-frame fusion + GPU enhancement (Real-ESRGAN x4v3, 0.7) + Lanczos. "
                                 "Best measured on the 1080p direct stream.")
        self.btn_best.clicked.connect(self._best_preset)
        v.addWidget(self.btn_best)
        card, cb = self._card("Mode", "", "tune-variant")
        r = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for i, (txt, key, ic_name) in enumerate((("Original 1:1", "original", "magnify-scan"),
                                                 ("Fast", "fast", "lightning-bolt-outline"),
                                                 ("Enhanced (GPU)", "enhanced", "creation"))):
            rb = QRadioButton(txt)
            rb.setIcon(T.icon(ic_name, T.MUTED))
            rb.setProperty("mode", key)
            self.mode_group.addButton(rb, i)
            r.addWidget(rb)
        r.addStretch(1)
        cb.addLayout(r)
        self.chk_temporal = QCheckBox("Multi-frame fusion (removes stream compression noise)")
        self.chk_temporal.setToolTip("Combines neighbouring real frames, aligned by optical flow. Never smears motion.")
        self.chk_temporal.toggled.connect(self._on_temporal)
        cb.addWidget(self.chk_temporal)
        v.addWidget(card)
        card, cb = self._card("Fine tuning", "", "knob")
        fl = QFormLayout()
        fl.setHorizontalSpacing(14)
        fl.setVerticalSpacing(10)
        self.sld_sharp = QSlider(Qt.Orientation.Horizontal)
        self.sld_sharp.setRange(0, 100)
        fl.addRow("Sharpening", self.sld_sharp)
        self.sld_strength = QSlider(Qt.Orientation.Horizontal)
        self.sld_strength.setRange(0, 100)
        fl.addRow("Enhance strength", self.sld_strength)
        self.cmb_model = QComboBox()
        self.cmb_model.addItem("realesr-general-x4v3 (fast)", "realesr-general-x4v3")
        self.cmb_model.addItem("RealESRGAN_x2plus (heavy)", "RealESRGAN_x2plus")
        fl.addRow("AI model", self.cmb_model)
        self.cmb_interp = QComboBox()
        self.cmb_interp.addItem("Lanczos (sharper)", "lanczos")
        self.cmb_interp.addItem("Bicubic (softer)", "cubic")
        fl.addRow("Resize filter", self.cmb_interp)
        cb.addLayout(fl)
        self.gpu_lbl = QLabel("GPU model not loaded")
        self.gpu_lbl.setObjectName("muted")
        self.gpu_lbl.setWordWrap(True)
        cb.addWidget(self.gpu_lbl)
        v.addWidget(card)
        v.addStretch(1)
        add_page("Quality", "creation", page)

        # ---- Output page
        page, v = self._page("Output", "The clean “FaceFrame Output” window you add to TikTok LIVE Studio "
                                       "(Add source → Window capture).")
        self.btn_output = self._btn("Start output", "cast", "primary")
        self.btn_output.setCheckable(True)
        self.btn_output.setMinimumHeight(40)
        self.btn_output.toggled.connect(self._toggle_output)
        v.addWidget(self.btn_output)
        card, cb = self._card("Size", "Match the panel size inside your LIVE Studio canvas (1080×1920 portrait: "
                              "1080×720 across the full width).", "aspect-ratio")
        fl = QFormLayout()
        fl.setHorizontalSpacing(14)
        fl.setVerticalSpacing(10)
        self.cmb_size = QComboBox()
        for w, h in settings_mod.OUTPUT_PRESETS:
            self.cmb_size.addItem(f"{w} × {h}", f"{w}x{h}")
        self.cmb_size.addItem("Custom", "")
        fl.addRow("Preset", self.cmb_size)
        r = QHBoxLayout()
        self.spn_w = QSpinBox()
        self.spn_h = QSpinBox()
        for sp in (self.spn_w, self.spn_h):
            sp.setRange(64, 3840)
            sp.setSingleStep(2)
        r.addWidget(self.spn_w, 1)
        xl = QLabel("×")
        xl.setObjectName("muted")
        r.addWidget(xl)
        r.addWidget(self.spn_h, 1)
        fl.addRow("Pixels", r)
        r2 = QHBoxLayout()                 # second row keeps the page narrow on small screens
        self.cmb_aspect = QComboBox()
        self.cmb_aspect.addItem("free", None)
        for k in settings_mod.ASPECTS:
            self.cmb_aspect.addItem(k, k)
        r2.addWidget(self.cmb_aspect, 1)
        self.btn_apply_size = self._btn("Apply", "check")
        self.btn_apply_size.clicked.connect(self._apply_custom_size)
        r2.addWidget(self.btn_apply_size)
        fl.addRow("Aspect", r2)
        self.cmb_fps = QComboBox()
        for f in (30, 60):
            self.cmb_fps.addItem(f"{f} fps", f)
        fl.addRow("Frame rate", self.cmb_fps)
        cb.addLayout(fl)
        v.addWidget(card)
        card, cb = self._card("Verify in LIVE Studio", "Shows a test pattern; record a few seconds in LIVE Studio "
                              "and run tools\\check_pattern.py to detect hidden scaling or clipping.", "grid")
        self.chk_pattern = QCheckBox("Show test pattern instead of video")
        self.chk_pattern.toggled.connect(self._toggle_pattern)
        cb.addWidget(self.chk_pattern)
        v.addWidget(card)
        card, cb = self._card("Sharpness advice", "", "lightbulb-on-outline")
        self.size_advice = QLabel("Load a stream and select the webcam to get advice.")
        self.size_advice.setWordWrap(True)
        cb.addWidget(self.size_advice)
        self.btn_size_fix = self._btn("Use 1080 × 720 (fits a 1080-wide portrait canvas)", "auto-fix", "primary")
        self.btn_size_fix.clicked.connect(lambda: self.cmb_size.setCurrentIndex(self.cmb_size.findData("1080x720")))
        cb.addWidget(self.btn_size_fix)
        v.addWidget(card)
        self.out_lbl = QLabel("")
        self.out_lbl.setObjectName("muted")
        self.out_lbl.setWordWrap(True)
        v.addWidget(self.out_lbl)
        v.addStretch(1)
        add_page("Output", "cast", page)

        # ---- Extension page
        page, v = self._page("Extension", "Optional Edge extension for decoded pixels from the Twitch tab. "
                                          "Pairing is automatic.")
        card, cb = self._card("Connection", "", "puzzle-outline")
        self.bridge_lbl = QLabel("")
        self.bridge_lbl.setWordWrap(True)
        cb.addWidget(self.bridge_lbl)
        r = QHBoxLayout()
        self.token_edit = QLineEdit(self.s.bridge.token)
        self.token_edit.setReadOnly(True)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        r.addWidget(self.token_edit, 1)
        b = self._btn("", "content-copy", "ghost")
        b.setToolTip("Copy pairing code")
        b.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.s.bridge.token))
        r.addWidget(b)
        b2 = self._btn("", "eye-outline", "ghost")
        b2.setToolTip("Show pairing code")
        b2.setCheckable(True)
        b2.toggled.connect(lambda on: self.token_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        r.addWidget(b2)
        cb.addLayout(r)
        v.addWidget(card)
        v.addStretch(1)
        add_page("Extension", "puzzle-outline", page)

        # ---- Diagnostics page
        page, v = self._page("Diagnostics", "Live measurements: sizes, rates, latency, memory.")
        card, cb = self._card("Pipeline", "", "chart-line")
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("mono")
        self.status_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_lbl.setWordWrap(True)
        cb.addWidget(self.status_lbl)
        v.addWidget(card)
        card, cb = self._card("Updates", f"FaceFrame {__version__}. New versions are offered automatically when "
                              "FaceFrame starts.", "update")
        self.btn_check_update = self._btn("Check for updates", "update")
        self.btn_check_update.clicked.connect(lambda: self.updates.check(manual=True))
        cb.addWidget(self.btn_check_update)
        v.addWidget(card)
        v.addStretch(1)
        add_page("Diagnostics", "chart-line", page)

        # the settings side never gets narrower than its widest page (pages don't scroll sideways)
        need = max(self.pages.widget(i).widget().minimumSizeHint().width() for i in range(self.pages.count()))
        self.pages.setMinimumWidth(need + 16)
        self.nav.setCurrentRow(0)

        self.menuBar().setVisible(False)
        for i in range(7):   # Ctrl+1..7 switch settings pages
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda k=i: self.nav.setCurrentRow(k))
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.showMinimized)   # hide controls; output keeps running

    def _set_chip(self, key: str, text: str, state: str = "idle") -> None:
        c = self.chips[key]
        if c.text() != text:
            c.setText(text)
        if c.property("state") != state:
            c.setProperty("state", state)
            c.style().unpolish(c)
            c.style().polish(c)

    def _load_into_ui(self) -> None:
        s = self.s
        f, q, o = s.framing, s.quality, s.output
        widgets = (self.chk_auto, self.chk_lock, self.cmb_framing, self.cmb_fill, self.sld_smooth, self.cmb_lost,
                   self.spn_grace, self.sld_sharp, self.sld_strength, self.cmb_model, self.cmb_size, self.cmb_fps)
        for w in widgets:
            w.blockSignals(True)
        self.chk_auto.setChecked(f.auto_frame)
        self.chk_lock.setChecked(f.lock)
        self.cmb_framing.setCurrentIndex(max(0, self.cmb_framing.findData(f.framing)))
        self.cmb_fill.setCurrentIndex(0 if f.fill else 1)
        self.sld_smooth.setValue(int(f.smoothness * 100))
        self.cmb_lost.setCurrentIndex(max(0, self.cmb_lost.findData(f.after_grace)))
        self.spn_grace.setValue(f.grace_s)
        self.spn_delay.blockSignals(True)
        self.spn_delay.setValue(s.source.direct_delay)
        self.spn_delay.blockSignals(False)
        self.mode_group.button({"original": 0, "fast": 1, "enhanced": 2}[q.mode]).setChecked(True)
        self.sld_sharp.setValue(int(q.sharpen * 100))
        self.sld_strength.setValue(int(q.enhance_strength * 100))
        self.cmb_model.setCurrentIndex(max(0, self.cmb_model.findData(q.sr_model)))
        self.cmb_interp.setCurrentIndex(max(0, self.cmb_interp.findData(q.interpolation)))
        self.chk_temporal.blockSignals(True)
        self.chk_temporal.setChecked(q.temporal)
        self.chk_temporal.blockSignals(False)
        idx = self.cmb_size.findData(f"{o.width}x{o.height}")
        self.cmb_size.setCurrentIndex(idx if idx >= 0 else self.cmb_size.count() - 1)
        self.spn_w.setValue(o.width)
        self.spn_h.setValue(o.height)
        self.cmb_fps.setCurrentIndex(max(0, self.cmb_fps.findData(o.fps)))
        for w in widgets:
            w.blockSignals(False)
        self.pipeline.tracker.cfg.grace_s = f.grace_s
        self.pipeline.tracker.cfg.after_grace = f.after_grace
        # change handlers
        self.chk_auto.toggled.connect(lambda v: self._set("framing", "auto_frame", v, auto_frame=v))
        self.chk_lock.toggled.connect(lambda v: self._set("framing", "lock", v, lock=v))
        self.cmb_framing.currentIndexChanged.connect(
            lambda _: self._set("framing", "framing", self.cmb_framing.currentData(), framing=self.cmb_framing.currentData()))
        self.cmb_fill.currentIndexChanged.connect(
            lambda _: self._set("framing", "fill", self.cmb_fill.currentData(), fill=self.cmb_fill.currentData()))
        self.sld_smooth.valueChanged.connect(lambda v: self._set("framing", "smoothness", v / 100, smoothness=v / 100))
        self.cmb_lost.currentIndexChanged.connect(self._on_lost_changed)
        self.spn_grace.valueChanged.connect(self._on_lost_changed)
        self.mode_group.idToggled.connect(self._on_mode)
        self.sld_sharp.valueChanged.connect(lambda v: self._set_q("sharpen", v / 100))
        self.sld_strength.valueChanged.connect(lambda v: self._set_q("enhance_strength", v / 100))
        self.cmb_model.currentIndexChanged.connect(self._on_model)
        self.cmb_interp.currentIndexChanged.connect(self._on_interp)
        self.cmb_size.currentIndexChanged.connect(self._on_size_preset)
        self.cmb_aspect.currentIndexChanged.connect(self._on_aspect)
        self.cmb_fps.currentIndexChanged.connect(self._on_fps)
        a = self.s.audio
        self.sld_vol.setValue(int(round(a.volume * 100)))
        self.vol_lbl.setText(f"{self.sld_vol.value()} %")
        self.chk_mute.setChecked(a.muted)
        self.chk_audio.setChecked(a.enabled)
        self._fill_audio_devices()
        self.sld_vol.valueChanged.connect(self._on_volume)
        self.chk_mute.toggled.connect(self._on_mute)
        self.chk_audio.toggled.connect(self._on_audio_enabled)
        self.cmb_audio_dev.currentIndexChanged.connect(self._on_audio_device)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._toggle_pause)

    # ============================================================== settings
    def _set(self, section: str, key: str, value, **pipe_kw) -> None:
        setattr(getattr(self.s, section), key, value)
        if pipe_kw:
            self.pipeline.update_config(**pipe_kw)
        self._save_timer.start(800)

    def _set_q(self, key: str, value) -> None:
        setattr(self.s.quality, key, value)
        setattr(self.pipeline.cfg.quality, key, value)
        self._save_timer.start(800)

    def _on_lost_changed(self, *_):
        self.s.framing.after_grace = self.cmb_lost.currentData()
        self.s.framing.grace_s = self.spn_grace.value()
        self.pipeline.tracker.cfg.after_grace = self.s.framing.after_grace
        self.pipeline.tracker.cfg.grace_s = self.s.framing.grace_s
        self._save_timer.start(800)

    def save_settings(self) -> None:
        try:
            settings_mod.save(self.s)
        except Exception as e:
            log.warning("could not save settings: %s", e)

    # ================================================================ source
    def select_source(self) -> None:
        dlg = SourcePickerDialog(self, preselect_title=self.s.source.title)
        if dlg.exec() != SourcePickerDialog.DialogCode.Accepted:
            return
        wi = dlg.selected()
        if wi is None:
            return
        self._start_wgc(wi.hwnd)

    def _start_wgc(self, hwnd: int, restore_panel: bool = False) -> None:
        try:
            src = WgcSource(hwnd, 0, max_fps=60)
        except SourceRefused as e:
            QMessageBox.warning(self, "Source refused", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Capture error", f"Could not start window capture: {e}")
            return
        self.pipeline.set_source(src)
        h = self.s.source
        same = h.kind == "wgc" and h.title == src.window.title
        h.kind, h.exe, h.title, h.hwnd, h.pid = "wgc", src.window.exe, src.window.title, src.window.hwnd, src.window.pid
        if not (restore_panel and same):
            h.panel_norm = None
        self._restore_pending = {"kind": "wgc"} if (restore_panel and h.panel_norm) else None
        self.save_settings()
        self.tabs.setCurrentWidget(self.view_source)
        if not self._restore_pending:
            self.notice("Source bound to this Edge window. Now press “Select webcam area” and drag around the webcam inset.")
            self.btn_area.setChecked(True)

    def use_native(self) -> None:
        if self.bridge is None or self.bridge.error:
            QMessageBox.warning(self, "Bridge unavailable", self.bridge.error if self.bridge else "Bridge not started")
            return
        src = BridgeSource(self.bridge, fps=self.s.output.fps)
        self.pipeline.set_source(src)
        h = self.s.source
        restoring = h.kind == "native" and bool(h.panel_norm)
        if h.kind != "native":
            h.panel_norm = None
        h.kind = "native"
        self._restore_pending = {"kind": "native"} if restoring else None
        self.save_settings()
        self.tabs.setCurrentWidget(self.view_source)
        self.notice("Native video: in the Twitch tab click the FaceFrame Bridge toolbar button → Activate on this tab, "
                    "then confirm the player on the page. First time: paste the pairing code (Copy) in the popup.")

    def use_direct(self, target: str | None = None, quality: str | None = None, restoring: bool = False) -> None:
        from PySide6.QtWidgets import QInputDialog
        h = self.s.source
        if not isinstance(target, str):
            target = None
        if target is None:
            target, ok = QInputDialog.getText(
                self, "Direct Twitch stream",
                "Twitch channel name or URL (live channel, or VOD link; use Twitch's Share > copy link at the "
                "current time to start a VOD where you are):", text=h.direct_target)
            if not ok or not target.strip():
                return
        try:
            normalize_target(target)
        except ValueError as e:
            QMessageBox.warning(self, "Direct stream", str(e))
            return
        if quality is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            err = ""
            try:
                qs = list_qualities(target)
            except Exception as e:
                qs = []
                err = str(e)
            finally:
                QApplication.restoreOverrideCursor()
            if not qs:
                QMessageBox.warning(self, "Direct stream", "No stream found (offline, private or wrong URL). " + err)
                return
            items = ["best"] + [q for q in reversed(qs) if q not in ("best", "audio_only")]
            quality, ok = QInputDialog.getItem(self, "Quality", "Rendition to decode (best = highest):", items, 0, False)
            if not ok:
                return
        self.edit_direct.setText(target)
        src = DirectStreamSource(target, quality, self.spn_delay.value())
        src.max_fps = float(self.s.output.fps)      # don't convert frames the output would drop
        self.perf.configure_source(src)
        self.pipeline.set_source(src)
        same = h.kind == "direct" and h.direct_target == target
        h.kind, h.direct_target, h.direct_quality = "direct", target, quality
        h.title = src.channel
        if not (restoring and same):
            h.panel_norm = None
        self._restore_pending = {"kind": "direct"} if (restoring and same and h.panel_norm) else None
        self.save_settings()
        self.tabs.setCurrentWidget(self.view_source)
        if not self._restore_pending:
            self.notice("Connecting to Twitch (takes ~10 s). When the picture appears, press “Select webcam area” and "
                        "drag around the inset. Use 'video delay' to line the picture up with the sound you hear.")
            self.btn_area.setChecked(True)

    # ------------------------------------------------------------- playback/audio
    def read_audio(self, n: int, dac_wall: float):
        """Audio callback provider: samples of the current direct source, or silence."""
        import numpy as _np
        src = self.pipeline.source
        a = self.s.audio
        if not a.enabled or a.muted or not isinstance(src, DirectStreamSource):
            return _np.zeros((n, 2), _np.float32)
        src.volume = a.volume
        return src.read_audio(n, dac_wall)

    def _fill_audio_devices(self) -> None:
        self.cmb_audio_dev.blockSignals(True)
        self.cmb_audio_dev.clear()
        try:
            devs = output_devices()
        except Exception as e:
            devs = [(-1, "Windows default output")]
            self.audio_status.setText(f"Audio devices unavailable: {e}")
        for idx, name in devs:
            self.cmb_audio_dev.addItem(name, name if idx >= 0 else "")
        i = self.cmb_audio_dev.findData(self.s.audio.device_name)
        self.cmb_audio_dev.setCurrentIndex(max(0, i))
        self.cmb_audio_dev.blockSignals(False)

    def _audio_device_index(self) -> int:
        name = self.s.audio.device_name
        if not name:
            return -1
        for idx, n in output_devices():
            if n == name:
                return idx
        return -1

    def _ensure_audio(self) -> None:
        want = self.s.audio.enabled and isinstance(self.pipeline.source, DirectStreamSource)
        if want and not self.audio.running:
            self.audio.device = self._audio_device_index()
            self.audio.start()
        elif not want and self.audio.running:
            self.audio.stop()

    def _on_volume(self, v: int) -> None:
        self.s.audio.volume = v / 100.0
        self.vol_lbl.setText(f"{v} %")
        self._save_timer.start(800)

    def _on_mute(self, on: bool) -> None:
        self.s.audio.muted = on
        self._save_timer.start(800)

    def _on_audio_enabled(self, on: bool) -> None:
        self.s.audio.enabled = on
        self._ensure_audio()
        self._save_timer.start(800)

    def _on_audio_device(self, *_):
        self.s.audio.device_name = self.cmb_audio_dev.currentData() or ""
        if self.audio.running:
            self.audio.set_device(self._audio_device_index())
        self._save_timer.start(800)

    def _toggle_pause(self) -> None:
        src = self.pipeline.source
        if not isinstance(src, DirectStreamSource):
            self.notice("Pause/Resume works with the Direct Twitch stream source.", warn=True)
            return
        if src.paused:
            src.resume()
        else:
            src.pause()

    @staticmethod
    def _hms(sec) -> str:
        if sec is None:
            return "--:--:--"
        sec = int(sec)
        return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

    def _update_playback(self) -> None:
        src = self.pipeline.source
        vod = isinstance(src, DirectStreamSource) and src.is_vod
        for w in (self.spn_jh, self.spn_jm, self.spn_js, self.btn_jump, self.btn_goto):
            w.setEnabled(vod)
        if not isinstance(src, DirectStreamSource):
            self.time_lbl.setText("--:--:-- / --:--:--")
            self.remain_lbl.setText("for Twitch links")
            self.btn_pause.setEnabled(False)
            self.jump_hint.setText("Load a past broadcast (twitch.tv/videos/…) to use this.")
            return
        if vod and src.duration_s:
            self.jump_hint.setText(f"Video length {self._hms(src.duration_s)}. Live streams can't jump.")
        elif not vod:
            self.jump_hint.setText("This is a live stream: jumping works on past broadcasts (twitch.tv/videos/…).")
        self.btn_pause.setEnabled(True)
        t = src.timing()
        paused = t["paused"]
        if self.btn_pause.accessibleName() != ("Resume" if paused else "Pause"):
            self.btn_pause.setAccessibleName("Resume" if paused else "Pause")
            self.btn_pause.setIcon(T.icon("play" if paused else "pause", "white"))
        if t["live"]:
            self.time_lbl.setText(f"● LIVE   {self._hms(t['position'])}")
            self.remain_lbl.setText("time watched" + (" · PAUSED" if t["paused"] else ""))
        else:
            self.time_lbl.setText(f"{self._hms(t['position'])} / {self._hms(t['duration'])}")
            self.remain_lbl.setText(f"-{self._hms(t['remaining'])} left" + (" · PAUSED" if t["paused"] else ""))
        a = self.audio
        if self.s.audio.enabled:
            name = self.cmb_audio_dev.currentText()
            self.audio_status.setText(
                (f"<span style='color:#ff8080'>{a.error}</span>" if a.error else
                 f"Audio → {name} · latency {a.latency_s * 1000:.0f} ms · in sync with the picture"
                 + (" · muted" if self.s.audio.muted else "")))
        else:
            self.audio_status.setText("FaceFrame audio off (use the Edge tab's sound instead)")

    def _focus_jump(self) -> None:
        src = self.pipeline.source
        if isinstance(src, DirectStreamSource) and src.is_vod:
            pos = int(src.timing().get("position") or 0)
            self.spn_jh.setValue(pos // 3600)
            self.spn_jm.setValue(pos % 3600 // 60)
            self.spn_js.setValue(pos % 60)
        self.nav.setCurrentRow(0)
        self.spn_jh.setFocus()
        self.spn_jh.selectAll()

    def _jump(self) -> None:
        src = self.pipeline.source
        if not (isinstance(src, DirectStreamSource) and src.is_vod):
            self.notice("Jumping works on past broadcasts (twitch.tv/videos/… links).", warn=True)
            return
        pos = self.spn_jh.value() * 3600 + self.spn_jm.value() * 60 + self.spn_js.value()
        if src.duration_s and pos >= src.duration_s:
            self.notice(f"That is past the end of the video ({self._hms(src.duration_s)}).", warn=True)
            return
        if src.seek(pos):
            self.notice(f"Jumping to {self._hms(pos)} … (takes a few seconds)")

    def jump_to(self, text: str) -> bool:
        """'1:02:03' / '1h2m3s' style input (also used by tests)."""
        sec = parse_hms(text)
        if sec is None:
            return False
        sec = int(sec)
        self.spn_jh.setValue(min(99, sec // 3600))
        self.spn_jm.setValue(sec % 3600 // 60)
        self.spn_js.setValue(sec % 60)
        self._jump()
        return True

    def _on_delay(self, v: float) -> None:
        self.s.source.direct_delay = v
        if isinstance(self.pipeline.source, DirectStreamSource):
            self.pipeline.source.set_delay(v)
        self._save_timer.start(800)

    def resume_source(self) -> None:
        h = self.s.source
        if h.kind == "direct" and h.direct_target:
            self.use_direct(h.direct_target, h.direct_quality, restoring=True)
            self.btn_resume.setVisible(False)
            return
        if h.kind == "native":
            self.use_native()
            return
        if h.kind == "wgc" and win.is_window(h.hwnd) and win.window_title(h.hwnd) == h.title:
            self._start_wgc(h.hwnd, restore_panel=True)
            self.notice("Resumed the previous Edge window. The saved webcam area is shown but the output stays "
                        "neutral until it is confirmed (automatically if the picture matches, or press Confirm).")
        else:
            self.select_source()
        self.btn_resume.setVisible(False)

    def _offer_resume(self) -> None:
        h = self.s.source
        if h.kind == "wgc" and h.hwnd and win.is_window(h.hwnd) and win.window_title(h.hwnd) == h.title \
                and win.window_pid(h.hwnd) == h.pid:
            self.btn_resume.setText(f"Resume: {h.title[:60]}")
            self.btn_resume.setVisible(True)
        elif h.kind == "direct" and h.direct_target:
            short = h.direct_target.replace("https://", "").replace("www.", "")
            short = short if len(short) <= 34 else short[:31] + "…"
            self.btn_resume.setText(f"Resume: {short} ({h.direct_quality})")
            self.btn_resume.setToolTip(h.direct_target)
            self.btn_resume.setVisible(True)
        elif h.kind == "native":
            self.btn_resume.setText("Resume native video (extension)")
            self.btn_resume.setVisible(True)
        elif h.kind == "wgc" and h.title:
            self.notice("The previous Edge window is gone. Choose “Edge window…” on the Source page (the matching title is preselected).")

    def stop_source(self) -> None:
        self.pipeline.stop_source()
        self.notice("Source stopped; capture resources released. Edge keeps running.")

    def _recover_action(self) -> None:
        st = self.pipeline.source_status.state
        src = self.pipeline.source
        if st == SourceState.MINIMIZED and isinstance(src, WgcSource):
            win.restore_window(src.hwnd)
        elif st in (SourceState.CLOSED, SourceState.FAILED):
            self.select_source()

    # ============================================================= selection
    def _toggle_area_select(self, on: bool) -> None:
        if on:
            self.btn_person.setChecked(False)
            self.tabs.setCurrentWidget(self.view_source)
            self.view_source.set_mode("select")
            if isinstance(self.pipeline.source, BridgeSource):
                self.pipeline.source.set_overview_rate(1.0)
            self.notice("Drag a rectangle around the webcam inset in the Source view.")
        else:
            self.view_source.set_mode("none")
            if isinstance(self.pipeline.source, BridgeSource):
                self.pipeline.source.set_overview_rate(0.0)

    def _select_area_in_page(self) -> None:
        src = self.pipeline.source
        if isinstance(src, BridgeSource):
            src.request_in_page_selection()
            self.notice("Drag the rectangle on the Twitch player in Edge.")
        else:
            self.notice("In-page selection needs the Edge extension source.", warn=True)

    def _on_panel_rect(self, r: Rect) -> None:
        ov = self.pipeline.latest_overview
        if ov is None:
            return
        panel = r.scaled(1.0 / ov.scale)
        sw, sh = ov.source_size
        panel = Rect(max(0, panel.x), max(0, panel.y), min(panel.w, sw - max(0, panel.x)), min(panel.h, sh - max(0, panel.y)))
        self._apply_panel(panel, ov)
        self.btn_area.setChecked(False)

    def _apply_panel(self, panel: Rect, ov, reference=None) -> None:
        self.pipeline.set_panel(panel, reference=reference)
        sw, sh = ov.source_size
        n = to_normalized(panel, sw, sh)
        h = self.s.source
        h.panel_norm = [n.x, n.y, n.w, n.h]
        h.panel_surface = [sw, sh]
        if reference is None:
            # save a small grayscale reference so a restart can verify the area
            g = cv2.cvtColor(ov.pixels, cv2.COLOR_BGR2GRAY)
            x, y, w, hh = panel.scaled(ov.scale).to_int()
            ref = g[max(0, y):y + hh, max(0, x):x + w]
            if ref.size:
                cv2.imwrite(str(settings_mod.settings_dir() / "panel_ref.png"), ref)
                h.panel_ref_scale = ov.scale
        self.save_settings()
        px = panel.to_int()
        self.notice(f"Webcam area set: {px[2]}x{px[3]} source px. The only face is preselected automatically; "
                    "use “Select person” to choose another.")
        self._auto_offer_done = False
        self.tabs.setCurrentWidget(self.view_panel)

    def _maybe_restore_panel(self) -> None:
        if not self._restore_pending:
            return
        ov = self.pipeline.latest_overview
        if ov is None:
            return
        h = self.s.source
        self._restore_pending = None
        if not h.panel_norm or list(ov.source_size) != list(h.panel_surface or []):
            self.notice("Source size differs from when the webcam area was saved: please select the webcam area "
                        "again.", warn=True)
            self.btn_area.setChecked(True)
            return
        panel = from_normalized(Rect(*h.panel_norm), *ov.source_size)
        ref_path = settings_mod.settings_dir() / "panel_ref.png"
        ref = cv2.imread(str(ref_path), cv2.IMREAD_GRAYSCALE) if ref_path.is_file() else None
        reference = (ref, h.panel_ref_scale, tuple(ov.source_size)) if ref is not None and h.panel_ref_scale else None
        self._apply_panel(panel, ov, reference=reference)
        if reference is None:
            self.btn_confirm.setVisible(True)

    def _confirm_restored(self) -> None:
        self.pipeline.confirm_panel()
        self.btn_confirm.setVisible(False)

    def _toggle_person_select(self, on: bool) -> None:
        if on:
            self.btn_area.setChecked(False)
            self.tabs.setCurrentWidget(self.view_panel)
            self.view_panel.set_mode("click")
            self.notice("Click the person to follow in the Webcam panel view.")
        else:
            self.view_panel.set_mode("none")

    def _on_person_click(self, x: float, y: float) -> None:
        if self.pipeline.select_person_at(x, y):
            self.notice("Person selected. FaceFrame follows this face only; it will not switch to another face.")
            self.btn_person.setChecked(False)
            self._auto_offer_done = True
        else:
            self.notice("No detected face at that point. Wait for the blue candidate boxes and click inside one.",
                        warn=True)

    def _reset_tracking(self) -> None:
        self.pipeline.reset_tracking()
        self._auto_offer_done = False
        self.notice("Tracking reset: searching. The only visible face will be preselected again, or click a person.")

    # =============================================================== quality
    def _on_mode(self, bid: int, checked: bool) -> None:
        if not checked:
            return
        mode = ["original", "fast", "enhanced"][bid]
        if not self._applying_tier and mode != self.s.quality.mode:
            self.perf.user_override("the quality mode")
        self.s.quality.mode = mode
        self.pipeline.cfg.quality.mode = QualityMode(mode)
        if mode == "enhanced":
            self.pipeline.renderer.reset_fallback()
            self._ensure_engine()
        self._save_timer.start(800)

    def _on_temporal(self, on: bool) -> None:
        if not self._applying_tier and on != self.s.quality.temporal:
            self.perf.user_override("multi-frame fusion")
        self.s.quality.temporal = on
        self.pipeline.update_config(temporal=on, temporal_strength=self.s.quality.temporal_strength)
        self._save_timer.start(800)

    def _on_interp(self, *_):
        if not self._applying_tier:
            self.perf.user_override("the resize filter")
        self._set_q("interpolation", self.cmb_interp.currentData())

    def _apply_tier(self, t) -> None:
        """Performance level -> Quality controls + pipeline + stream cap + preview rate."""
        self._applying_tier = True
        try:
            self.mode_group.button(2 if t.mode == "enhanced" else 1).setChecked(True)
            self.chk_temporal.setChecked(t.temporal)
            self.cmb_interp.setCurrentIndex(max(0, self.cmb_interp.findData(t.interpolation)))
            self.pipeline.cfg.quality.light = t.light
            self.pipeline.update_config(detect_hz=t.detect_hz)
            src = self.pipeline.source
            if isinstance(src, DirectStreamSource):
                src.set_max_height(t.max_height)
            self.timer.setInterval(max(33, int(1000 / t.preview_hz)))
        finally:
            self._applying_tier = False

    def _best_preset(self) -> None:
        """Best measured combination on the direct 1080p stream (evidence/quality_direct)."""
        if self.perf.auto:
            self.perf.best()
            return
        self.cmb_model.setCurrentIndex(max(0, self.cmb_model.findData("realesr-general-x4v3")))
        self.cmb_interp.setCurrentIndex(max(0, self.cmb_interp.findData("lanczos")))
        self.sld_strength.setValue(70)
        self.sld_sharp.setValue(15)
        self.chk_temporal.setChecked(True)
        self.mode_group.button(2).setChecked(True)   # Enhanced (loads the GPU model if needed)
        self.pipeline.renderer.reset_fallback()
        hint = ""
        if not isinstance(self.pipeline.source, DirectStreamSource):
            hint = " For the most real detail, also use the Direct Twitch stream source (best = 1080p60)."
        self.notice("Best quality preset: multi-frame fusion + GPU enhancement (x4v3, 0.7) + Lanczos." + hint)

    def _on_model(self, *_):
        name = self.cmb_model.currentData()
        self.s.quality.sr_model = name
        self.pipeline.cfg.quality.sr_model = name
        if self.engine is not None and self.engine.model_name != name:
            self.pipeline.set_sr_engine(None)
            old, self.engine = self.engine, None
            threading.Thread(target=old.close, daemon=True).start()
        if self.s.quality.mode == "enhanced":
            self._ensure_engine()
        self._save_timer.start(800)

    def _ensure_engine(self) -> None:
        if self.engine is not None or self._engine_loading:
            return
        self._engine_loading = True
        name = self.s.quality.sr_model
        self.gpu_lbl.setText(f"Loading {name} on the GPU…")

        def work():
            try:
                from ..core.sr_engine import SREngine
                eng = SREngine(name, self.mdir)
                eng.load()
                self.engineLoaded.emit(eng, "")
            except Exception as e:  # missing model, no CUDA, OOM, driver errors
                self.engineLoaded.emit(None, str(e))

        threading.Thread(target=work, name="sr-load", daemon=True).start()

    def _on_engine_loaded(self, eng, err: str) -> None:
        self._engine_loading = False
        if eng is None:
            if "CUDA" in err or "PyTorch" in err:
                err = "no compatible NVIDIA GPU on this PC — Fast mode is used (still the full stream quality)"
            self.gpu_lbl.setText(f"Enhanced unavailable: {err}.")
            self.notice(f"GPU enhancement unavailable: {err}", warn=True)
            return
        if eng.model_name != self.s.quality.sr_model:
            eng.close()
            self._ensure_engine()
            return
        self.engine = eng
        self.pipeline.set_sr_engine(eng)
        i = eng.info
        self.gpu_lbl.setText(f"{i.model} x{i.scale} on {i.gpu_name}, {i.precision}"
                             f"{f' (fp16 vs fp32 PSNR {i.fp16_psnr_db:.1f} dB)' if i.fp16_psnr_db else ''}, "
                             f"loaded in {i.load_ms:.0f} ms. {i.licence}.")

    def _on_compare_mode(self, mode: str) -> None:
        self.pipeline.update_config(compare_mode=QualityMode(mode))
        if mode == "enhanced":
            self._ensure_engine()

    def _on_tab(self, idx: int) -> None:
        comparing = self.tabs.widget(idx) is not self.view_source and self.tabs.widget(idx) is not self.view_panel
        if comparing and self.cmp_mode.currentText() == self.s.quality.mode:
            # comparing a mode with itself shows nothing: pick the other one
            self.cmp_mode.setCurrentText("fast" if self.s.quality.mode != "fast" else "enhanced")
        self.pipeline.update_config(compare=comparing, compare_mode=QualityMode(self.cmp_mode.currentText()))
        if comparing and self.cmp_mode.currentText() == "enhanced":
            self._ensure_engine()
        src = self.pipeline.source
        on_source = self.tabs.widget(idx) is self.view_source
        if isinstance(src, BridgeSource) and not self.btn_area.isChecked():
            src.set_overview_rate(0.5 if on_source else 0.0)
        elif isinstance(src, (WgcSource, DirectStreamSource)):
            src.set_overview_rate(15.0 if on_source else 2.0)

    def _save_compare(self) -> None:
        cf = self._compare_frozen or self.pipeline.compare_slot.peek()[0]
        if cf is None:
            self.notice("Open the Compare tab while output is running first.", warn=True)
            return
        d = WORK_ROOT / "evidence" / "compare"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        a = d / f"{stamp}_frame{cf.src_seq}_{cf.label_a.split()[0]}.png"
        b = d / f"{stamp}_frame{cf.src_seq}_{cf.label_b.split()[0]}.png"
        cv2.imwrite(str(a), cf.a)
        cv2.imwrite(str(b), cf.b)
        self.notice(f"Saved same-frame comparison ({cf.crop_size[0]}x{cf.crop_size[1]} crop): {a.name}, {b.name}")

    # ================================================================ output
    def _on_size_preset(self, *_):
        data = self.cmb_size.currentData()
        if not data:
            return
        wh = tuple(int(v) for v in data.split("x"))
        self.spn_w.setValue(wh[0])
        self.spn_h.setValue(wh[1])
        self._set_output_size(*wh)

    def _on_aspect(self, *_):
        k = self.cmb_aspect.currentData()
        if k:
            h = int(round(self.spn_w.value() / settings_mod.ASPECTS[k]))
            self.spn_h.setValue(h + (h % 2))

    def _apply_custom_size(self) -> None:
        w, h = self.spn_w.value(), self.spn_h.value()
        w += w % 2
        h += h % 2
        self.spn_w.setValue(w)
        self.spn_h.setValue(h)
        self._set_output_size(w, h)

    def _set_output_size(self, w: int, h: int) -> None:
        self.s.output.width, self.s.output.height = w, h
        self.pipeline.update_config(out_w=w, out_h=h)
        self.output.set_output_size(w, h)
        self._save_timer.start(500)

    def _on_fps(self, *_):
        f = self.cmb_fps.currentData()
        self.s.output.fps = f
        self.pipeline.update_config(out_fps=f)
        if isinstance(self.pipeline.source, DirectStreamSource):
            self.pipeline.source.max_fps = float(f)
        if isinstance(self.pipeline.source, BridgeSource):
            self.bridge.send({"type": "config", "fps": f})
        if self.output_running:
            self.output.start(f)
        self._save_timer.start(500)

    def _toggle_pattern(self, on: bool) -> None:
        self.output.test_pattern = on
        if on:
            from ..core.testpattern import make
            d = WORK_ROOT / "evidence"
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"test_pattern_{self.s.output.width}x{self.s.output.height}.png"
            cv2.imwrite(str(p), make(self.s.output.width, self.s.output.height))
            self.notice(f"Test pattern on. In LIVE Studio, record a few seconds locally, then run: "
                        rf"tools\check_pattern.py <recording.mp4> --size {self.s.output.width}x{self.s.output.height}. "
                        f"Reference saved: {p.name}")

    def _toggle_output(self, on: bool) -> None:
        if on:
            o = self.s.output
            if o.x is not None and o.y is not None:
                self.output.move(o.x, o.y)
            self.output.show()
            self.output.start(o.fps)
            self.output_running = True
            self.btn_output.setText("Stop output")
            self.btn_output.setIcon(T.icon("cast-off", "white"))
        else:
            pos = self.output.pos()
            self.s.output.x, self.s.output.y = pos.x(), pos.y()
            self.output.stop()
            self.output.hide()
            self.output_running = False
            self.btn_output.setText("Start output")
            self.btn_output.setIcon(T.icon("cast", "white"))
            self.save_settings()

    # ============================================================ periodic
    def _update_advice(self, st: dict) -> None:
        crop = st.get("crop_px")
        if not crop:
            return
        cw, chh = crop
        ow, oh = self.s.output.width, self.s.output.height
        scale = st.get("scale") or (oh / chh)
        lines = [f"The face crop has <b>{cw}×{chh}</b> real pixels from the stream; the output is "
                 f"<b>{ow}×{oh}</b> = <b>{scale:.1f}×</b> enlargement."]
        if self.s.quality.mode != "enhanced":
            lines.append("Turn on <b>Best quality</b> (Quality page) to let the GPU upscale it.")
        if ow > 1080:
            lines.append("Your LIVE Studio canvas is 1080 px wide, so LIVE Studio shrinks this window anyway — "
                         "a 1080×720 output keeps the same sharpness with less enlargement.")
        if scale > 4:
            lines.append("Above 4× the AI model's own factor, the rest is interpolation. For a sharper face: "
                         "Framing → <b>Wider</b> uses more of the webcam's pixels.")
        self.size_advice.setText("<br>".join(lines))
        self.btn_size_fix.setVisible(ow > 1080)

    def _update_chips(self, st: dict) -> None:
        src = self.pipeline.source
        state = st["source_state"]
        kind = ("DIRECT " + (src.rendition or "") if isinstance(src, DirectStreamSource) else
                "EXTENSION" if isinstance(src, BridgeSource) else "EDGE WINDOW" if isinstance(src, WgcSource) else "")
        if src is None:
            self._set_chip("source", "●  NO SOURCE", "idle")
        else:
            lvl = {"RUNNING": "ok", "STARTING": "accent", "STALLED": "warn", "WAITING": "warn",
                   "MINIMIZED": "warn"}.get(state, "err" if state in ("CLOSED", "FAILED") else "idle")
            self._set_chip("source", f"●  {kind.strip()}  {state}", lvl)
        tr = st["track_state"] if src is not None and self.pipeline.panel is not None else "NO TARGET"
        self._set_chip("track", f"●  {tr}", {"TRACKING": "ok", "LOST": "warn", "SEARCHING": "accent"}.get(tr, "idle"))
        pv = st["panel_validity"] if self.pipeline.panel is not None else "NOT SET"
        self._set_chip("area", f"●  AREA {pv}", {"VALID": "ok", "CHECKING": "accent", "INVALID": "err"}.get(pv, "idle"))
        mode = (st["mode_used"] or self.s.quality.mode).upper()
        if self.s.quality.temporal:
            mode += " + FUSION"
        if st.get("crop_px") and st.get("scale"):
            cw, chh = st["crop_px"]
            mode += f"  ×{st['scale']:.1f} from {cw}×{chh}"
        self._set_chip("quality", f"★  {mode}", "accent" if "ENHANCED" in mode else "idle")
        if self.output_running:
            self._set_chip("output", f"●  ON AIR  {self.s.output.width}×{self.s.output.height}", "ok")
        else:
            self._set_chip("output", "●  OUTPUT OFF", "idle")
        fps = st["process_fps"]
        self._set_chip("fps", f"{fps:.0f} FPS" if fps else "— FPS", "ok" if fps >= 25 else ("warn" if fps else "idle"))

    def notice(self, text: str, warn: bool = False) -> None:
        self.notice_lbl.setText(text)
        self.notice_frame.setProperty("level", "warn" if warn else "info")
        self.notice_icon.setPixmap(T.icon("alert-outline" if warn else "information-outline",
                                          T.WARN if warn else "#9cc0ff").pixmap(18, 18))
        self.notice_frame.style().unpolish(self.notice_frame)
        self.notice_frame.style().polish(self.notice_frame)
        self.notice_frame.setVisible(bool(text))

    def _tick(self) -> None:
        p = self.pipeline
        st = p.status()
        self._maybe_restore_panel()
        # auto-preselect a single clear candidate (confirmation = it is shown; click another to change)
        if not self._auto_offer_done and p.panel is not None and p.tracker.state == TrackState.SEARCHING:
            c = p.tracker.single_clear_candidate()
            if c is not None:
                p.select_detection(c.box)
                self._auto_offer_done = True
                self.notice("Main face found and selected (green). Use “Select person” to pick someone else, or "
                            "“Reset / reacquire” to search again.")
        self.btn_confirm.setVisible(p.require_confirmed_panel and p.panel is not None)
        self._update_playback()
        self._ensure_audio()
        self._update_views()
        self._update_status(st)

    def _update_views(self) -> None:
        p = self.pipeline
        w = self.tabs.currentWidget()
        if w is self.view_source:
            ov = p.latest_overview
            if ov is not None:
                self.view_source.set_image(ov.pixels)
                ovl = []
                if p.panel is not None:
                    col = {PanelValidity.VALID: "#3ddc84", PanelValidity.CHECKING: "#ffd400",
                           PanelValidity.INVALID: "#ff4d4d"}[p.panel_validity]
                    ovl.append(Overlay(p.panel.scaled(ov.scale), col, f"webcam area ({p.panel_validity.value})"))
                self.view_source.overlays = ovl
                kind = "rendered window" if ov.source_kind.value.startswith("wgc") else "decoded video"
                self.view_source.caption = (f"{kind}: {ov.source_size[0]}x{ov.source_size[1]} px "
                                            f"(preview downscaled to {ov.pixels.shape[1]}x{ov.pixels.shape[0]})")
        elif w is self.view_panel:
            f = p.latest_frame
            if f is not None:
                self.view_panel.set_image(f.pixels)
                ovl = [Overlay(d.box, "#3B82F6", f"face {d.score:.2f}") for d in p.tracker.candidates]
                if p.tracker.target is not None:
                    ovl.append(Overlay(p.tracker.target, "#22C55E", "following", 3))
                out = p._last_out
                if out is not None and out.crop is not None:
                    ovl.append(Overlay(out.crop, "#9479FF", "output frame", 2, True))
                self.view_panel.overlays = ovl
                info = out.info if out else None
                kind = "rendered-window" if f.source_kind.value.startswith("wgc") else "decoded-video"
                self.view_panel.caption = (f"panel {f.size[0]}x{f.size[1]} {kind} px"
                                           + (f" · crop {info.crop_size[0]}x{info.crop_size[1]} → "
                                              f"{info.out_size[0]}x{info.out_size[1]} ({info.scale:.1f}x)" if info else ""))
        else:
            cf = p.compare_slot.peek()[0]
            if self.cmp_freeze.isChecked():
                cf = self._compare_frozen or cf
                self._compare_frozen = cf
            else:
                self._compare_frozen = None
            if cf is not None:
                self.view_cmp_a.set_image(cf.a)
                self.view_cmp_b.set_image(cf.b)
                self.view_cmp_a.caption = f"{cf.label_a} · frame {cf.src_seq} · crop {cf.crop_size[0]}x{cf.crop_size[1]}"
                self.view_cmp_b.caption = f"{cf.label_b} · same frame {cf.src_seq}"

    def _update_status(self, st: dict) -> None:
        p = self.pipeline
        src = p.source
        self._update_chips(st)
        self._update_advice(st)
        state = st["source_state"]
        self.src_status.setText(f"<b>{state}</b> {st['source_msg']}"
                                + (f"<br><i>{st['source_recovery']}</i>" if st["source_recovery"] else ""))
        rec = ""
        if state == "MINIMIZED":
            rec = "Restore Edge window"
        elif state in ("CLOSED", "FAILED"):
            rec = "Select another source…"
        self.btn_recover.setText(rec)
        self.btn_recover.setVisible(bool(rec))
        self.btn_area_page.setEnabled(isinstance(src, BridgeSource))
        os_ = self.output.stats()
        ok = "OK" if os_["size_ok"] else "MISMATCH"
        self.out_lbl.setText(
            f"Window title: <b>{OUTPUT_TITLE}</b> · client {os_['client_px'][0]}x{os_['client_px'][1]} physical px "
            f"({ok}, configured {self.s.output.width}x{self.s.output.height})"
            + (" · <span style='color:#ff8080'>MINIMIZED: LIVE Studio receives no new frames; restore it (covering "
               "it with other windows is fine)</span>" if os_["minimized"] else ""))
        b = self.bridge
        if b is not None:
            self.bridge_lbl.setText(
                (f"<span style='color:#ff8080'>{b.error}</span>" if b.error else
                 f"Listening on 127.0.0.1:{b.port} · extension {'connected' if b.connected else 'not connected'}"
                 + (f" · clock RTT {fmt((b.clock.best() or (None,))[0], '.2f')} ms" if b.connected else "")))
        kind = st["source_kind"] or "-"
        lines = [
            f"source      {kind}  {st['source_size'] or ''}"
            + (f"  decoded video {st['video_size'][0]}x{st['video_size'][1]}" if st['video_size'] else
               ("  (decoded video size not measurable via window capture)" if kind.startswith("wgc") else "")),
            f"panel       {st['panel_px'] or '-'} px  validity {st['panel_validity']} ({fmt(st['panel_score'], '.2f')})",
            f"tracking    {st['track_state']}  candidates {st['candidates']}  detect {fmt(st['detect_hz'])} Hz "
            f"{fmt(st['detect_ms'])} ms",
            f"crop        {st['crop_px'] or '-'} -> drawn {st['drawn_px'] or '-'} in {st['out_px']} "
            f"(x{fmt(st['scale'], '.2f')})",
            f"quality     {st['mode_used'] or '-'}" + (f"  SR {fmt(st['sr_ms'])} ms" if st['mode_used'] == 'enhanced' else "")
            + (f"  fusion {fmt(st['fuse_ms'])} ms" if st.get('fuse_ms') else "")
            + (f"  shimmer ratio {fmt(st['flicker_ratio'], '.2f')}" if st['flicker_ratio'] else ""),
            f"rates       capture {fmt(st['capture_fps'])} (unique {fmt(st['unique_source_fps'])})  "
            f"processed {fmt(st['process_fps'])}  "
            f"output painted {fmt(os_['output_unique_fps'])} fps",
            f"latency     capture->processed p50 {fmt(st['latency_p50'])} p95 {fmt(st['latency_p95'])} ms  "
            f"capture->painted p50 {fmt(os_['paint_latency_p50'])} p95 {fmt(os_['paint_latency_p95'])} ms",
            f"            clock: {st['latency_clock'] or '-'}  queue age p95 {fmt(st['queue_age_p95'])} ms",
            f"queues      superseded (by design) process {st['dropped_process']} detect {st['dropped_detect']}  "
            f"stale detections {st['stale_detections']}",
        ]
        if isinstance(src, BridgeSource):
            bs = src.stats()
            lines.append(f"native      readback p50 {fmt(bs['readback_p50'])} ms  present->receive p50 "
                         f"{fmt(bs['present_to_receive_p50'])} ms  {getattr(src, 'colour_conversion', '')}")
        try:
            import psutil
            rss = psutil.Process().memory_info().rss / 2 ** 20
            mem = f"RAM {rss:.0f} MB"
        except Exception:
            mem = ""
        if self.engine is not None:
            mm = self.engine.memory_mb()
            if mm:
                mem += f"  VRAM (torch) alloc {mm[0]:.0f} / reserved {mm[1]:.0f} MB"
        lines.append(f"memory      {mem}")
        if st["panel_px"] and st["crop_px"]:
            pw_, ph_ = st["panel_px"]
            if kind.startswith("wgc"):
                lines.append(f"source px   Edge draws the webcam at {pw_}x{ph_} px (rendered). More detail: use the "
                             f"Edge extension, or make the player bigger (Twitch theatre mode / F11), quality = Source")
            elif st["video_size"]:
                lines.append(f"source px   webcam {pw_}x{ph_} decoded px of {st['video_size'][0]}x{st['video_size'][1]}"
                             f" video. More detail only from a higher Twitch quality setting")
        warn = st["warning"] or ""
        reason = st["mode_reason"] or st["fallback"] or ""
        if reason:
            lines.append(f"note        {reason}")
        if warn:
            lines.append(f"warning     {warn}")
        if st["panel_event"]:
            lines.append(f"panel event {st['panel_event']}")
        self.status_lbl.setText("\n".join(lines))
        if reason and self.s.quality.mode == "enhanced" and p.renderer.forced_fast:
            self.gpu_lbl.setText(f"Fallback active: {reason}. Select Enhanced again to retry.")

    # ================================================================== exit
    def closeEvent(self, e) -> None:  # noqa: N802
        if self.output_running:
            pos = self.output.pos()
            self.s.output.x, self.s.output.y = pos.x(), pos.y()
        self.save_settings()
        self.timer.stop()
        self.audio.stop()
        self.output.stop()
        self.output.close()
        self.pipeline.shutdown()
        if self.bridge is not None:
            self.bridge.stop()
        log.info("FaceFrame closed cleanly")
        super().closeEvent(e)
        QApplication.quit()


def apply_palette(app: QApplication) -> None:
    """Explicit dark palette (Fusion + Windows dark mode otherwise fills unchecked
    check/radio indicators with the accent colour, which reads as 'checked')."""
    from PySide6.QtGui import QColor, QPalette
    pal = QPalette()
    c = {QPalette.ColorRole.Window: (37, 37, 42), QPalette.ColorRole.WindowText: (232, 232, 238),
         QPalette.ColorRole.Base: (24, 24, 28), QPalette.ColorRole.AlternateBase: (44, 44, 50),
         QPalette.ColorRole.ToolTipBase: (50, 50, 58), QPalette.ColorRole.ToolTipText: (232, 232, 238),
         QPalette.ColorRole.Text: (232, 232, 238), QPalette.ColorRole.Button: (52, 52, 60),
         QPalette.ColorRole.ButtonText: (232, 232, 238), QPalette.ColorRole.BrightText: (255, 90, 90),
         QPalette.ColorRole.Highlight: (107, 63, 212), QPalette.ColorRole.HighlightedText: (255, 255, 255),
         QPalette.ColorRole.Link: (140, 170, 255), QPalette.ColorRole.PlaceholderText: (140, 140, 150),
         QPalette.ColorRole.Accent: (107, 63, 212)}
    for role, rgb in c.items():
        pal.setColor(role, QColor(*rgb))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(120, 120, 128))
    app.setPalette(pal)
    app.setStyleSheet(T.stylesheet())
    app.setWindowIcon(T.app_icon())


def setup_logging() -> Path:
    d = settings_mod.settings_dir()
    path = d / "faceframe.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    root.addHandler(sh)
    return path


def selftest() -> dict:
    """Headless check of the installed runtime (used by the installer and for support).
    The GPU part is optional: FaceFrame runs in Fast mode without a CUDA GPU."""
    import json
    import platform
    res: dict = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "app_root": str(APP_ROOT),
                 "python": sys.version.split()[0], "windows": platform.version()}
    s, _ = settings_mod.load(Path(os.environ.get("TEMP", ".")) / "faceframe_selftest_settings.json")
    md = models_dir(s)
    res["models_dir"] = str(md)
    try:
        from ..core.detector import YuNetDetector
        YuNetDetector(md / "face_detection_yunet_2023mar.onnx").detect(np.zeros((190, 350, 3), np.uint8))
        res["detector"] = "ok"
    except Exception as e:
        res["detector"] = f"FAILED: {e}"
    for mod in ("PySide6", "cv2", "windows_capture", "streamlink", "av", "sounddevice", "websockets", "qtawesome"):
        try:
            __import__(mod)
            res[mod] = "ok"
        except Exception as e:
            res[mod] = f"FAILED: {e}"
    try:
        from ..core.sr_engine import SREngine
        e = SREngine("realesr-general-x4v3", md)
        i = e.load()
        t0 = time.perf_counter()
        out = e.run_resized(np.full((108, 192, 3), 128, np.uint8), 1280, 720, 0.7)
        res["gpu_enhance"] = {"gpu": i.gpu_name, "precision": i.precision, "out": list(out.shape),
                              "ms": round((time.perf_counter() - t0) * 1000, 1)}
        e.close()
    except Exception as e:
        res["gpu_enhance"] = f"unavailable: {e}"
    try:
        from ..capture.audio import output_devices
        res["audio_outputs"] = len(output_devices()) - 1
    except Exception as e:
        res["audio_outputs"] = f"FAILED: {e}"
    required = ("detector", "PySide6", "cv2", "windows_capture", "streamlink", "av", "sounddevice", "websockets",
                "qtawesome")
    res["ok"] = all(res.get(k) == "ok" for k in required)
    out_path = settings_mod.settings_dir() / "selftest.json"
    out_path.write_text(json.dumps(res, indent=1))
    res["written_to"] = str(out_path)
    return res


class _SingleInstance:
    """Named mutex: a second FaceFrame brings the running one to the front instead."""

    def __init__(self) -> None:
        import ctypes
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)   # last error captured right after the call
        self.handle = self._k.CreateMutexW(None, False, "Local\\FaceFrame.SingleInstance")
        self.already = ctypes.get_last_error() == 183   # ERROR_ALREADY_EXISTS

    @staticmethod
    def activate_existing() -> None:
        import ctypes
        from ..capture import windows as wmod
        for wi in wmod.list_top_windows(exclude_own=True):
            if wi.title.startswith("FaceFrame") and "Controls" in wi.title:
                ctypes.windll.user32.ShowWindow(wi.hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(wi.hwnd)
                return


def _install_excepthook() -> None:
    """Unexpected errors are logged with a traceback and shown, not silently fatal."""
    import traceback

    def hook(etype, value, tb):
        log.error("unhandled exception:\n%s", "".join(traceback.format_exception(etype, value, tb)))
        try:
            app = QApplication.instance()
            if app is not None:
                QMessageBox.critical(None, "FaceFrame error",
                                     f"{etype.__name__}: {value}\n\nDetails were written to the log:\n"
                                     f"{settings_mod.settings_dir() / 'faceframe.log'}")
        except Exception:
            pass

    sys.excepthook = hook
    threading.excepthook = lambda a: log.error("thread %s crashed:\n%s", a.thread.name if a.thread else "?",
                                                "".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                                                   a.exc_traceback)))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    try:   # own taskbar identity (FaceFrame icon instead of Python's)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FaceFrame.App")
    except Exception:
        pass
    if "--selftest" in argv:
        r = selftest()
        print(r)
        return 0 if r.get("ok") else 1
    logpath = setup_logging()
    _install_excepthook()
    inst = _SingleInstance()
    if inst.already and "--allow-multiple" not in argv:
        log.info("FaceFrame already running; activating it")
        _SingleInstance.activate_existing()
        return 0
    s, problems = settings_mod.load()
    app = QApplication(argv)
    app.setApplicationName("FaceFrame")
    app.setStyle("Fusion")
    apply_palette(app)
    w = ControlWindow(s, problems)
    w.show()
    log.info("FaceFrame %s started; log %s; models %s", __version__, logpath, w.mdir)
    return app.exec()
