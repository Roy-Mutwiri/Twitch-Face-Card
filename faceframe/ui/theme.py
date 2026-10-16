"""FaceFrame visual design: colour tokens, Qt stylesheet, icons, app icon.

Icons: Material Design Icons via QtAwesome (Apache-2.0 icons, MIT library).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)

# ------------------------------------------------------------------ tokens
BG = "#0F1115"          # window background
SURFACE = "#161920"     # panels
CARD = "#1C2029"        # cards
CARD_HI = "#232837"     # hovered / raised
BORDER = "#2A303D"
TEXT = "#E8EAF0"
MUTED = "#9AA3B5"
FAINT = "#6B7385"
ACCENT = "#7C5CFF"      # violet
ACCENT_HI = "#9479FF"
ACCENT_LO = "#5B3FE0"
BLUE = "#3B82F6"
OK = "#22C55E"
WARN = "#F59E0B"
ERR = "#EF4444"


def icon(name: str, color: str = TEXT, active: str | None = None) -> QIcon:
    import qtawesome as qta
    kw = {"color": color, "color_disabled": FAINT}
    if active:
        kw["color_active"] = active
    return qta.icon(f"mdi6.{name}", **kw)


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """FaceFrame logo: gradient tile, white framing corners around a head-and-shoulders silhouette."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0.0, QColor("#8B5CF6"))
    g.setColorAt(1.0, QColor("#2563EB"))
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.22, s * 0.22)
    p.fillPath(tile, g)
    # subtle top highlight
    hl = QLinearGradient(0, 0, 0, s * 0.6)
    hl.setColorAt(0.0, QColor(255, 255, 255, 46))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(tile, hl)
    # framing brackets
    pen = QPen(QColor("white"), s * 0.065, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    m, L = s * 0.22, s * 0.17
    for (x, y, dx, dy) in ((m, m, 1, 1), (s - m, m, -1, 1), (m, s - m, 1, -1), (s - m, s - m, -1, -1)):
        path = QPainterPath(QPointF(x, y + dy * L))
        path.lineTo(QPointF(x, y))
        path.lineTo(QPointF(x + dx * L, y))
        p.drawPath(path)
    # head and shoulders
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("white"))
    p.drawEllipse(QPointF(s * 0.5, s * 0.43), s * 0.115, s * 0.125)
    body = QPainterPath()
    body.moveTo(s * 0.30, s * 0.74)
    body.cubicTo(s * 0.31, s * 0.60, s * 0.41, s * 0.57, s * 0.5, s * 0.57)
    body.cubicTo(s * 0.59, s * 0.57, s * 0.69, s * 0.60, s * 0.70, s * 0.74)
    body.closeSubpath()
    p.drawPath(body)
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for sz in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(app_icon_pixmap(sz))
    return ic


def _check_png() -> str:
    """White check mark for checked checkboxes (QSS needs an image file)."""
    import os
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "faceframe_check_v1.png")
    if not os.path.exists(path):
        icon("check-bold", "white").pixmap(14, 14).save(path)
    return path.replace("\\", "/")


def stylesheet() -> str:
    check = _check_png()
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#central {{ background: {BG}; }}
QToolTip {{ background: {CARD_HI}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

/* header */
QFrame#header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel#appTitle {{ font-size: 14pt; font-weight: 600; letter-spacing: 0.3px; }}
QLabel#appSubtitle {{ color: {MUTED}; font-size: 9pt; }}
QLabel#chip {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 11px; padding: 3px 10px;
              color: {MUTED}; font-size: 9pt; font-weight: 600; }}
QLabel#chip[state="ok"] {{ color: {OK}; border-color: #1f5f3a; background: #13261c; }}
QLabel#chip[state="warn"] {{ color: {WARN}; border-color: #6b4a12; background: #2a2010; }}
QLabel#chip[state="err"] {{ color: {ERR}; border-color: #6b2222; background: #2a1414; }}
QLabel#chip[state="accent"] {{ color: {ACCENT_HI}; border-color: #3d2f80; background: #1d1838; }}

/* cards & panels */
QFrame#previewCard, QFrame#sideCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#cardHint, QLabel#muted {{ color: {MUTED}; font-size: 9pt; }}
QLabel#pageTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#pageSub {{ color: {MUTED}; }}
QLabel#mono {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 9pt; color: {MUTED}; }}
QLabel#timeBig {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 15pt; font-weight: 600; }}
QLabel#timeSmall {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 10pt; color: {MUTED}; }}

/* navigation */
QListWidget#nav {{ background: transparent; border: none; outline: 0; padding: 6px; }}
QListWidget#nav::item {{ padding: 10px 10px; margin: 2px 0; border-radius: 8px; color: {MUTED}; }}
QListWidget#nav::item:hover {{ background: {CARD}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: #241d47; color: {TEXT}; border-left: 3px solid {ACCENT}; }}

/* tabs (segmented) */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; margin-right: 4px;
               border: 1px solid transparent; border-radius: 8px; }}
QTabBar::tab:hover {{ background: {CARD}; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {CARD_HI}; color: {TEXT}; border: 1px solid {BORDER}; }}

/* buttons */
QPushButton {{ background: {CARD_HI}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 12px; }}
QPushButton:hover {{ background: #2b3142; border-color: #3a4152; }}
QPushButton:pressed {{ background: #1a1e27; }}
QPushButton:checked {{ background: #2a2152; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {FAINT}; background: {CARD}; border-color: {BORDER}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT_HI}; color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HI}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_LO}; }}
QPushButton[kind="primary"]:checked {{ background: {ERR}; border-color: #f87171; }}
QPushButton[kind="primary"]:disabled {{ background: {CARD_HI}; border-color: {BORDER}; color: {FAINT}; }}
QPushButton[kind="hero"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8B5CF6, stop:1 #3B82F6);
                           border: none; color: white; font-weight: 700; padding: 10px 14px; border-radius: 10px; }}
QPushButton[kind="hero"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9d75ff, stop:1 #5b9bff); }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[kind="ghost"]:hover {{ background: {CARD_HI}; border-color: {BORDER}; }}
QPushButton[kind="round"] {{ background: {ACCENT}; border: none; border-radius: 22px; min-width: 44px; max-width: 44px;
                            min-height: 44px; max-height: 44px; padding: 0; }}
QPushButton[kind="round"]:hover {{ background: {ACCENT_HI}; }}
QPushButton[kind="round"]:disabled {{ background: {CARD_HI}; }}
QPushButton[kind="danger"] {{ color: #fca5a5; }}
QPushButton[kind="danger"]:hover {{ background: #3a1c1c; border-color: #7f2a2a; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#bigInput {{ padding: 9px 10px; font-size: 10.5pt; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: #2a2152;
    outline: 0; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}

/* checks & radios */
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid #485066; background: {BG}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_HI}; image: url({check}); }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
    stop:0 white, stop:0.35 white, stop:0.45 {ACCENT}, stop:1 {ACCENT}); border-color: {ACCENT_HI}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT_HI}; }}

/* sliders */
QSlider::groove:horizontal {{ height: 4px; background: #2c3242; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8B5CF6, stop:1 #3B82F6);
    border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    border: 2px solid {ACCENT}; }}

/* scrollbars */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333a4a; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #434b5e; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}

/* transport + notice */
QFrame#transport {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#notice {{ background: #17223a; border: 1px solid #23365c; border-radius: 10px; }}
QFrame#notice[level="warn"] {{ background: #2a1f10; border-color: #6b4a12; }}
QLabel#noticeText {{ color: #dbe7ff; }}
QFrame#notice[level="warn"] QLabel#noticeText {{ color: #ffe2bf; }}
QSplitter::handle {{ background: transparent; width: 8px; }}
QMenuBar {{ background: {SURFACE}; }}
QMenuBar::item:selected {{ background: {CARD_HI}; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2a2152; }}
QListWidget {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; }}
QListWidget::item:selected {{ background: #2a2152; }}
QDialogButtonBox QPushButton {{ min-width: 80px; }}
"""
