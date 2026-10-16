"""Quality → Performance card: reads this PC's specs, picks the quality tier it can sustain
and keeps adjusting it while FaceFrame runs (see core/autotune.py)."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel

from ..capture.direct import DirectStreamSource
from ..core import autotune as A

log = logging.getLogger("faceframe.perf")


class PerformanceController(QObject):
    probed = Signal(object, str)

    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self.cfg = window.s.performance
        self.spec: A.SystemSpec | None = A.spec_from_dict(self.cfg.spec)
        self.start_tier = A.tier_index(self.cfg.tier, 2)
        self.gov: A.Governor | None = None
        self.current: int | None = None
        self._probing = False
        self.probed.connect(self._on_probed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(2000)

    # ------------------------------------------------------------------ UI
    def build_card(self):
        card, cb = self.w._card("Performance", "FaceFrame reads this PC's processor, memory and graphics card, picks "
                                "the highest quality it can run smoothly, and keeps adjusting while it runs.",
                                "speedometer")
        self.chk_auto = QCheckBox("Automatic: adapt to this PC (recommended)")
        self.chk_auto.setChecked(self.cfg.auto)
        self.chk_auto.toggled.connect(self._on_auto)
        cb.addWidget(self.chk_auto)
        self.pc_lbl = QLabel("Analysing this PC…")
        self.pc_lbl.setObjectName("cardHint")
        self.pc_lbl.setWordWrap(True)
        cb.addWidget(self.pc_lbl)
        self.tier_lbl = QLabel("")
        self.tier_lbl.setWordWrap(True)
        cb.addWidget(self.tier_lbl)
        r = QHBoxLayout()
        lab = QLabel("Level")
        lab.setObjectName("muted")
        r.addWidget(lab)
        self.cmb_tier = QComboBox()
        for t in A.TIERS:
            self.cmb_tier.addItem(t.name, t.name)
            self.cmb_tier.setItemData(self.cmb_tier.count() - 1, t.summary, 3)   # Qt.ToolTipRole
        self.cmb_tier.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_tier.setMinimumContentsLength(9)
        self.cmb_tier.setToolTip("With Automatic off, choose a fixed level here")
        self.cmb_tier.activated.connect(self._on_fixed_tier)
        r.addWidget(self.cmb_tier, 1)
        self.btn_recheck = self.w._btn("Re-check PC", "refresh")
        self.btn_recheck.setToolTip("Measure this PC again (after a hardware or driver change)")
        self.btn_recheck.clicked.connect(lambda: self.run_probe(force=True))
        r.addWidget(self.btn_recheck)
        cb.addLayout(r)
        self._refresh_labels()
        return card

    def _refresh_labels(self) -> None:
        if self.spec is not None:
            self.pc_lbl.setText("This PC: " + self.spec.describe())
        idx = self.current if self.current is not None else self.start_tier
        t = A.TIERS[idx]
        self.cmb_tier.blockSignals(True)
        self.cmb_tier.setCurrentIndex(idx)
        self.cmb_tier.blockSignals(False)
        self.cmb_tier.setEnabled(not self.cfg.auto)
        if self.cfg.auto:
            self.tier_lbl.setText(f"<b>{t.name}</b>: {t.summary}. Adjusts itself if the PC gets busy.")
        else:
            self.tier_lbl.setText(f"<b>Manual</b>: your own Quality settings are used"
                                  f"{'' if self.current is None else f' (last level: {t.name})'}.")

    # --------------------------------------------------------------- probe
    def start(self) -> None:
        if self.cfg.auto:
            if self.cfg.tier:                       # use the last level at once, refine after the probe
                self.apply(self.start_tier, "")
            self.run_probe()
        else:
            self._refresh_labels()

    def run_probe(self, force: bool = False) -> None:
        if self._probing:
            return
        self._probing = True
        self.pc_lbl.setText("Analysing this PC (about 2 seconds)…")
        cached = None if force else self.cfg.spec
        mdir = self.w.mdir

        def work():
            try:
                sp, err = A.probe(mdir, cached), ""
            except Exception as e:  # noqa: BLE001 - never block the app on the probe
                sp, err = None, str(e)
            try:
                self.probed.emit(sp, err)
            except RuntimeError:            # window closed while probing
                pass
        threading.Thread(target=work, name="pc-probe", daemon=True).start()

    def _on_probed(self, sp, err: str) -> None:
        self._probing = False
        if sp is None:
            log.warning("PC probe failed: %s", err)
            self.pc_lbl.setText(f"Could not analyse this PC ({err}); using Balanced.")
            sp = A.SystemSpec(threads=4)
        self.spec = sp
        self.cfg.spec = A.spec_to_dict(sp)
        self.start_tier = A.choose_tier(sp)
        log.info("PC: %s -> %s (bench balanced %.1f ms, light %.1f ms, detect %.1f ms)", sp.describe(),
                 A.TIERS[self.start_tier].name, sp.ms_balanced, sp.ms_light, sp.ms_detect)
        if self.cfg.auto:
            self.gov = A.Governor(self.start_tier, A.path_for(sp))
            self.apply(self.start_tier, "")
        self._refresh_labels()
        self.w.save_settings()

    # --------------------------------------------------------------- apply
    def apply(self, idx: int, reason: str) -> None:
        idx = max(0, min(len(A.TIERS) - 1, idx))
        t = A.TIERS[idx]
        if t.mode == "enhanced" and self.spec is not None and not self.spec.cuda:
            idx, t = 2, A.TIERS[2]                  # no GPU: best CPU level
        prev = self.current
        self.current = idx
        self.cfg.tier = t.name
        self.w._apply_tier(t)
        self._refresh_labels()
        if reason and prev is not None and prev != idx:
            better = idx < prev
            self.w.notice(f"Performance: {'raised' if better else 'lowered'} to {t.name} ({reason}). "
                          f"{'' if better else 'The output stays smooth; quality returns when the PC has room.'}",
                          warn=not better)

    def configure_source(self, src) -> None:
        """New direct source: start it at the current level's resolution cap."""
        if isinstance(src, DirectStreamSource) and self.current is not None and (self.cfg.auto or self.cfg.tier):
            src.max_height = A.TIERS[self.current].max_height

    def best(self) -> None:
        """'Best quality preset' with Automatic on: the best level this PC sustains."""
        if self.spec is not None:
            self.gov = A.Governor(self.start_tier, A.path_for(self.spec))
        self.apply(self.start_tier, "")
        self.w.notice(f"Best quality for this PC: {A.TIERS[self.start_tier].name} "
                      f"({A.TIERS[self.start_tier].summary}). It rises or falls automatically with the PC's load.")

    # -------------------------------------------------------------- toggles
    @property
    def auto(self) -> bool:
        return self.cfg.auto

    def user_override(self, what: str) -> None:
        """A manual Quality change: Automatic steps aside so the user's choice sticks."""
        if self.cfg.auto:
            self.chk_auto.setChecked(False)
            self.w.notice(f"Automatic performance turned off because you changed {what}. "
                          "Turn it back on in Quality → Performance.")

    def _on_auto(self, on: bool) -> None:
        self.cfg.auto = on
        if on:
            self.run_probe()
        else:
            self.gov = None
            src = self.w.pipeline.source
            if isinstance(src, DirectStreamSource):
                src.set_max_height(None)            # manual: full stream quality
            self.w.pipeline.cfg.quality.light = False
        self._refresh_labels()
        self.w.save_settings()

    def _on_fixed_tier(self, i: int) -> None:
        if self.cfg.auto:
            return
        self.apply(i, "")
        self.cfg.tier = A.TIERS[i].name
        self._refresh_labels()
        self.w.save_settings()

    # ------------------------------------------------------------ governor
    def _cpu_pct(self, psutil) -> float:
        """Load of the cores this process may use (all of them normally)."""
        per = psutil.cpu_percent(percpu=True)
        try:
            aff = psutil.Process().cpu_affinity() or range(len(per))
        except Exception:
            aff = range(len(per))
        vals = [per[i] for i in aff if i < len(per)]
        return sum(vals) / len(vals) if vals else 0.0

    def _own_pct(self, psutil) -> float:
        """FaceFrame's share of the cores it may use (0..100)."""
        try:
            pr = psutil.Process()
            if not hasattr(self, "_proc"):
                self._proc = pr
                pr.cpu_percent(None)
                return 0.0
            n = len(pr.cpu_affinity() or []) or psutil.cpu_count() or 1
            return self._proc.cpu_percent(None) / n
        except Exception:
            return 100.0

    def _tick(self) -> None:
        if not self.cfg.auto or self.gov is None or self.current is None:
            return
        try:
            self._govern()
        except Exception:  # noqa: BLE001 - the governor must never take the UI down or go silent
            log.exception("performance governor tick failed")

    def _govern(self) -> None:
        import time

        import psutil
        p = self.w.pipeline
        st = p.status()
        src = p.source
        out_fps = float(self.w.s.output.fps)
        uniq = st.get("unique_source_fps") or st.get("capture_fps") or 0.0
        expected = min(out_fps, uniq) if uniq else 0.0
        paused = bool(getattr(src, "paused", False))
        # judged only while output frames are expected: a followed face, or the webcam area itself
        # while searching (when nothing is published, e.g. neutral after a lost face, it isn't judged)
        tr = st.get("track_state")
        active = (src is not None and not paused and p.panel is not None
                  and st.get("panel_validity") == "VALID" and st.get("source_state") == "RUNNING"
                  and (tr == "TRACKING" or (tr == "SEARCHING" and (st.get("process_fps") or 0) > 5)))
        tier = A.TIERS[self.current]
        m = A.Metrics(
            t=time.monotonic(), expected_fps=expected, process_fps=st.get("process_fps") or 0.0,
            late_drops=getattr(src, "dropped_late", 0), rebuffers=getattr(src, "rebuffers", 0),
            cpu_pct=self._cpu_pct(psutil), lat95_ms=st.get("latency_p95") or 0.0,
            enhanced_requested=tier.mode == "enhanced" and not self.w._engine_loading,
            enhanced_running=st.get("mode_used") == "enhanced", active=active, own_pct=self._own_pct(psutil))
        new = self.gov.update(m)
        if m.active and m.expected_fps > 5 and m.process_fps < 1:
            log.warning("no output frames while active (level %s, area %s, track %s)", tier.name,
                        st.get("panel_validity"), tr)
        if new is not None and new != self.current:
            self.apply(new, self.gov.last_reason)
            self.w.save_settings()
