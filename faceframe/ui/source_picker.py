"""Edge window picker with identifying preview thumbnails (WGC one-shot grabs)."""

from __future__ import annotations

import threading

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

from ..capture import windows as win
from ..capture.wgc import grab_preview
from .qimage import bgr_to_pixmap


class SourcePickerDialog(QDialog):
    previewReady = Signal(int, object)

    def __init__(self, parent=None, preselect_title: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Source — Microsoft Edge window")
        self.resize(820, 620)
        lay = QVBoxLayout(self)
        info = QLabel(
            "Pick the Edge window that shows the Twitch player. Tip: put the Twitch tab in its own Edge window and "
            "keep that tab selected — window capture sees whatever tab is in front, and cannot follow a tab.\n"
            "Choose the highest stable quality in Twitch's own player settings and make the player large: window "
            "capture records rendered pixels, so a small player throws detail away before FaceFrame sees it.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.ListMode)
        self.list.setIconSize(QSize(240, 160))
        self.list.setSpacing(4)
        lay.addWidget(self.list, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.populate)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.addButton(self.refresh_btn, QDialogButtonBox.ButtonRole.ResetRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(bb)
        self.preselect_title = preselect_title
        self.previewReady.connect(self._set_preview)
        self.populate()

    def populate(self) -> None:
        self.list.clear()
        wins = win.list_edge_windows()
        if not wins:
            it = QListWidgetItem("No Microsoft Edge windows found. Open Twitch in Edge and press Refresh.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
            return
        for w in wins:
            it = QListWidgetItem(w.label())
            it.setData(Qt.ItemDataRole.UserRole, w)
            it.setSizeHint(QSize(760, 170))
            self.list.addItem(it)
            if self.preselect_title and w.title == self.preselect_title:
                self.list.setCurrentItem(it)
            threading.Thread(target=self._grab, args=(w.hwnd,), daemon=True).start()
        if self.list.currentItem() is None:
            self.list.setCurrentRow(0)

    def _grab(self, hwnd: int) -> None:
        img = grab_preview(hwnd)
        self.previewReady.emit(hwnd, img)

    def _set_preview(self, hwnd: int, img) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            w = it.data(Qt.ItemDataRole.UserRole)
            if w is not None and w.hwnd == hwnd:
                if img is None:
                    it.setText(it.text() + "\n(no preview: minimized or not rendering)")
                else:
                    it.setIcon(QIcon(bgr_to_pixmap(img)))

    def selected(self) -> win.WindowInfo | None:
        it = self.list.currentItem()
        return None if it is None else it.data(Qt.ItemDataRole.UserRole)
