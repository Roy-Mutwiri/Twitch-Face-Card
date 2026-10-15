"""Update prompt: "Update now" / "Skip" (skip = ask again next start), download progress,
hand-over to the silent installer, close + relaunch."""

from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout)

from .. import __version__, updater
from . import theme as T

log = logging.getLogger("faceframe.update")


class UpdateDialog(QDialog):
    def __init__(self, info: updater.UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FaceFrame update")
        self.setWindowIcon(T.app_icon())
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(T.app_icon_pixmap(48))
        head.addWidget(ic)
        tl = QVBoxLayout()
        t = QLabel(f"FaceFrame {info.version} is available")
        t.setObjectName("pageTitle")
        s = QLabel(f"You have {__version__}. Updating keeps your settings and takes about a minute; "
                   "FaceFrame closes and reopens by itself.")
        s.setObjectName("pageSub")
        s.setWordWrap(True)
        tl.addWidget(t)
        tl.addWidget(s)
        head.addLayout(tl, 1)
        v.addLayout(head)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "Bug fixes and improvements.")
        notes.setMinimumHeight(220)
        v.addWidget(notes, 1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.bar.setTextVisible(True)
        v.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setObjectName("cardHint")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_skip = QPushButton("Skip")
        self.btn_skip.setToolTip("Not now - FaceFrame asks again the next time it starts")
        self.btn_update = QPushButton("  Update now")
        self.btn_update.setIcon(T.icon("download", "white"))
        self.btn_update.setProperty("kind", "primary")
        self.btn_update.setDefault(True)
        row.addWidget(self.btn_skip)
        row.addWidget(self.btn_update)
        v.addLayout(row)


class UpdateController(QObject):
    """Owns the background check and the update flow for one ControlWindow."""

    found = Signal(object)
    progress = Signal(int, int)
    finished = Signal(object, str)          # (installer path | None, error)
    noUpdate = Signal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self._dlg: UpdateDialog | None = None
        self._info = None
        self._cancel = False
        self._manual = False
        self.found.connect(self._on_found)
        self.progress.connect(self._on_progress)
        self.finished.connect(self._on_finished)
        self.noUpdate.connect(self._on_no_update)

    @staticmethod
    def enabled() -> bool:
        if os.environ.get("FACEFRAME_NO_UPDATE_CHECK") == "1":
            return False
        return updater.install_dir() is not None or os.environ.get("FACEFRAME_UPDATE_CHECK") == "1"

    def check(self, manual: bool = False) -> None:
        self._manual = manual

        def work():
            info = updater.check(__version__)
            if info is not None:
                self.found.emit(info)
            elif manual:
                self.noUpdate.emit()
        threading.Thread(target=work, name="update-check", daemon=True).start()

    # ---------------------------------------------------------------- UI thread
    def _on_no_update(self) -> None:
        QMessageBox.information(self.w, "FaceFrame", f"FaceFrame {__version__} is up to date "
                                "(or GitHub could not be reached).")

    def _on_found(self, info) -> None:
        if self._dlg is not None:
            return
        self._info = info
        d = UpdateDialog(info, self.w)
        self._dlg = d
        d.btn_skip.clicked.connect(self._skip)
        d.btn_update.clicked.connect(self._start)
        d.rejected.connect(self._skip)
        d.setWindowModality(Qt.WindowModality.WindowModal)
        d.show()

    def _skip(self) -> None:
        if self._dlg is None:
            return
        self._cancel = True
        log.info("update %s skipped (asks again next start)", self._info.version if self._info else "?")
        self._dlg.hide()
        self._dlg.deleteLater()
        self._dlg = None

    def _start(self) -> None:
        d = self._dlg
        if updater.install_dir() is None:
            QMessageBox.information(self.w, "FaceFrame", "This copy was not installed with the installer.\n"
                                    f"Download the new version from:\n{self._info.page}")
            return
        d.btn_update.setEnabled(False)
        d.btn_skip.setText("Cancel")
        d.bar.setVisible(True)
        d.bar.setRange(0, 0)
        d.status.setText("Downloading the update…")
        self._cancel = False
        info = self._info

        def work():
            try:
                path = updater.download(info, lambda a, b: self.progress.emit(a, b), lambda: self._cancel)
                self.finished.emit(path, "")
            except Exception as e:  # noqa: BLE001
                self.finished.emit(None, str(e))
        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        if self._dlg is None:
            return
        if total:
            self._dlg.bar.setRange(0, 1000)
            self._dlg.bar.setValue(int(done * 1000 / total))
            self._dlg.bar.setFormat(f"{done / 2 ** 20:.0f} / {total / 2 ** 20:.0f} MB")

    def _on_finished(self, path, err: str) -> None:
        if self._dlg is None:            # cancelled / skipped meanwhile
            return
        if path is None:
            log.warning("update download failed: %s", err)
            self._dlg.status.setText(f"Update failed: {err}. Try again later, or download it from the website.")
            self._dlg.btn_update.setEnabled(True)
            self._dlg.btn_update.setText("  Try again")
            self._dlg.btn_skip.setText("Skip")
            self._dlg.bar.setVisible(False)
            return
        self._dlg.status.setText("Installing… FaceFrame will close now and reopen when the update is done.")
        self._dlg.repaint()
        try:
            updater.launch_installer(path, updater.install_dir(), updater.own_pids())
        except Exception as e:  # noqa: BLE001
            self._dlg.status.setText(f"Could not start the installer: {e}")
            return
        self._dlg.hide()
        self.w.close()
