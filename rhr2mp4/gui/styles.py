"""Centralised QSS stylesheet for the rhr2mp4 GUI.

Extracted from app.py for maintainability.  The palette stays true to
Rhythia's signature deep-blue / purple aesthetic but pushes contrast,
adds glassmorphism depth and glow accents, and styles every widget
state that Qt exposes (hover, pressed, disabled, focus, checked, …).
"""

from __future__ import annotations


def build_stylesheet() -> str:
    """Return the full application stylesheet."""
    return _STYLESHEET


_STYLESHEET = """
/* ===================================================================
   ROOT
   =================================================================== */

QWidget#root {
    background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
                                stop:0 #050d1e,
                                stop:0.45 #0a1533,
                                stop:1 #111d42);
    color: #e0e8ff;
    font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

/* ===================================================================
   SCROLLBAR (global)
   =================================================================== */

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(130, 150, 255, 0.25);
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(130, 150, 255, 0.45);
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: none;
    height: 0;
}

QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: rgba(130, 150, 255, 0.25);
    border-radius: 4px;
    min-width: 32px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(130, 150, 255, 0.45);
}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background: none;
    width: 0;
}

/* ===================================================================
   CARDS — frosted-glass panels
   =================================================================== */

QFrame#cardPanel {
    background-color: rgba(12, 20, 48, 0.82);
    border: 1px solid rgba(100, 120, 255, 0.16);
    border-radius: 18px;
}

QFrame#headerCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 rgba(18, 28, 62, 0.92),
                                stop:1 rgba(10, 18, 48, 0.92));
    border: 1px solid rgba(110, 135, 255, 0.18);
    border-radius: 18px;
}

QFrame#footerBar {
    background-color: rgba(7, 12, 30, 0.97);
    border-top: 1px solid rgba(100, 120, 255, 0.18);
}

/* ===================================================================
   TYPOGRAPHY
   =================================================================== */

QLabel#headerTitle {
    color: #ffffff;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: -0.5px;
}

QLabel#headerSubtitle {
    color: rgba(170, 190, 255, 0.8);
    font-size: 12px;
    font-weight: 400;
    letter-spacing: 0.3px;
}

QLabel#sectionTitle {
    color: #96a8ff;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1.2px;
}

QPushButton#collapseToggle {
    background: transparent;
    border: none;
    color: #96a8ff;
    font-size: 13px;
    font-weight: 700;
    padding: 0;
}

QPushButton#collapseToggle:hover {
    color: #c0d0ff;
}

QLabel#fieldLabel {
    color: #b0bfff;
    font-size: 12px;
    font-weight: 500;
}

QLabel#subsectionLabel {
    color: rgba(150, 170, 235, 0.6);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    border-top: 1px solid rgba(120, 140, 255, 0.14);
    padding-top: 10px;
    margin-top: 2px;
}

QLabel#metaLabel {
    color: rgba(150, 170, 235, 0.85);
    font-size: 12px;
    font-weight: 400;
}

QLabel#mapInfoTitle {
    color: #f0f4ff;
    font-size: 13px;
    font-weight: 700;
}

QLabel#statusLabel {
    color: #c8d4ff;
    font-size: 13px;
    font-weight: 400;
}

QLabel#etaLabel {
    color: rgba(170, 190, 255, 0.75);
    font-size: 12px;
    font-weight: 400;
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
}

QLabel#logoCaption {
    color: #f6f8ff;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: -0.4px;
    qproperty-alignment: AlignCenter;
}

QLabel#logoSubtitle {
    color: rgba(160, 175, 255, 0.75);
    font-size: 11px;
    font-weight: 400;
    qproperty-alignment: AlignCenter;
}

/* ===================================================================
   TEXT INPUTS — QLineEdit, QComboBox, QDoubleSpinBox
   =================================================================== */

QLineEdit, QComboBox, QDoubleSpinBox {
    background-color: rgba(16, 26, 56, 0.95);
    color: #dce4ff;
    border: 1px solid rgba(100, 120, 255, 0.18);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 13px;
    min-height: 32px;
    selection-background-color: rgba(110, 130, 255, 0.35);
}

QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover {
    border: 1px solid rgba(120, 145, 255, 0.32);
    background-color: rgba(20, 30, 62, 0.98);
}

QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
    border: 1px solid rgba(130, 150, 255, 0.55);
    background-color: rgba(20, 30, 62, 0.98);
}

QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {
    color: rgba(160, 175, 220, 0.45);
    background-color: rgba(14, 22, 48, 0.6);
    border: 1px solid rgba(80, 95, 180, 0.12);
}

/* ComboBox drop-down arrow area */
QComboBox::drop-down {
    border: none;
    width: 28px;
    padding-right: 6px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid rgba(150, 170, 255, 0.6);
    width: 0;
    height: 0;
    margin-right: 8px;
}

/* ComboBox popup list — solid colors for Linux Qt5 compat */
QComboBox QAbstractItemView {
    background-color: #0e1634;
    color: #dce4ff;
    border: 1px solid #1e2d5a;
    border-radius: 0px;
    padding: 4px 0px;
    outline: none;
    selection-background-color: #253570;
    selection-color: #ffffff;
}

QComboBox QAbstractItemView::item {
    padding: 6px 12px;
    min-height: 26px;
}

QComboBox QAbstractItemView::item:selected {
    background-color: #253570;
}

QComboBox QAbstractItemView::item:hover {
    background-color: #1a2850;
}

/* SpinBox buttons */
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: transparent;
    border: none;
    width: 18px;
}

QDoubleSpinBox::up-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid rgba(150, 170, 255, 0.55);
    width: 0; height: 0;
}

QDoubleSpinBox::down-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid rgba(150, 170, 255, 0.55);
    width: 0; height: 0;
}

/* ===================================================================
   CHECKBOX
   =================================================================== */

QCheckBox {
    spacing: 8px;
    color: #c8d6ff;
    font-size: 13px;
}

QCheckBox:hover {
    color: #e0eaff;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid rgba(130, 155, 255, 0.30);
    background: rgba(18, 28, 60, 0.9);
}

QCheckBox::indicator:hover {
    border: 1px solid rgba(140, 165, 255, 0.50);
    background: rgba(24, 35, 72, 0.95);
}

QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                 stop:0 #7b6aff, stop:1 #5499ff);
    border: 1px solid rgba(120, 140, 255, 0.50);
}

QCheckBox:disabled {
    color: rgba(160, 175, 220, 0.4);
}

QCheckBox::indicator:disabled {
    background: rgba(14, 22, 48, 0.5);
    border: 1px solid rgba(80, 95, 180, 0.12);
}

/* ===================================================================
   GENERIC BUTTONS
   =================================================================== */

QPushButton {
    background-color: rgba(18, 30, 65, 0.92);
    color: #d0dcff;
    border: 1px solid rgba(100, 120, 255, 0.18);
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 600;
    font-size: 13px;
}

QPushButton:hover {
    background-color: rgba(28, 42, 82, 0.96);
    border: 1px solid rgba(120, 145, 255, 0.30);
    color: #e8f0ff;
}

QPushButton:pressed {
    background-color: rgba(22, 36, 75, 0.98);
    border: 1px solid rgba(110, 130, 255, 0.35);
}

QPushButton:disabled {
    color: rgba(140, 160, 210, 0.35);
    background-color: rgba(14, 22, 48, 0.5);
    border: 1px solid rgba(80, 95, 180, 0.10);
}

/* ===================================================================
   RENDER BUTTON — gradient glow
   =================================================================== */

QPushButton#renderButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #7b6aff, stop:0.5 #6a8bff, stop:1 #52a2ff);
    color: #ffffff;
    border: 2px solid rgba(130, 160, 255, 0.30);
    border-radius: 14px;
    font-size: 15px;
    font-weight: 700;
    padding: 14px 24px;
    letter-spacing: 0.3px;
}

QPushButton#renderButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #8d7fff, stop:0.5 #7e9cff, stop:1 #68b5ff);
    border: 2px solid rgba(150, 180, 255, 0.45);
}

QPushButton#renderButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #6655dd, stop:0.5 #5570dd, stop:1 #4088dd);
}

QPushButton#renderButton:disabled {
    background: rgba(40, 50, 80, 0.6);
    color: rgba(180, 195, 240, 0.4);
    border: 1px solid rgba(80, 95, 180, 0.15);
}

/* Cancel state — red-tinted */
QPushButton#renderButton[renderState="cancel"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #cc4455, stop:1 #aa3355);
    border: 2px solid rgba(255, 100, 120, 0.35);
}

QPushButton#renderButton[renderState="cancel"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #dd5566, stop:1 #bb4466);
    border: 2px solid rgba(255, 120, 140, 0.50);
}

/* ===================================================================
   SECONDARY BUTTON (open folder, etc.)
   =================================================================== */

QPushButton#secondaryButton {
    background-color: rgba(22, 35, 72, 0.90);
    color: #c0cfff;
    border: 1px solid rgba(110, 135, 255, 0.20);
    border-radius: 10px;
    padding: 9px 16px;
    font-weight: 600;
    font-size: 13px;
}

QPushButton#secondaryButton:hover {
    background-color: rgba(32, 48, 92, 0.96);
    border: 1px solid rgba(130, 155, 255, 0.32);
    color: #e0eaff;
}

QPushButton#secondaryButton:disabled {
    color: rgba(140, 160, 210, 0.30);
    background-color: rgba(14, 22, 48, 0.45);
    border: 1px solid rgba(80, 95, 180, 0.08);
}

/* ===================================================================
   DROP FIELDS — drag-and-drop buttons
   =================================================================== */

/* Empty — dashed border, muted text */
QPushButton[dropState="empty"] {
    background-color: rgba(14, 22, 50, 0.85);
    border: 1.5px dashed rgba(110, 135, 255, 0.32);
    color: rgba(140, 165, 255, 0.75);
    border-radius: 10px;
    font-weight: 500;
}

QPushButton[dropState="empty"]:hover {
    background-color: rgba(20, 30, 62, 0.95);
    border: 1.5px dashed rgba(140, 165, 255, 0.55);
    color: #d0dcff;
}

/* Drag-over — vivid highlight */
QPushButton[dropState="drag"] {
    background-color: rgba(65, 80, 170, 0.75);
    border: 2px dashed rgba(160, 180, 255, 0.80);
    color: #ffffff;
    border-radius: 10px;
}

/* File loaded — subtle filled state */
QPushButton[dropState="has_file"] {
    background-color: rgba(16, 26, 54, 0.92);
    border: 1px solid rgba(90, 115, 220, 0.28);
    color: #dce4ff;
    text-align: left;
    padding-left: 14px;
    border-radius: 10px;
    font-weight: 500;
}

QPushButton[dropState="has_file"]:hover {
    background-color: rgba(22, 34, 68, 0.96);
    border: 1px solid rgba(110, 135, 255, 0.38);
}

/* ===================================================================
   PROGRESS BAR
   =================================================================== */

QProgressBar {
    background-color: rgba(18, 28, 60, 0.90);
    color: #eaf0ff;
    font-size: 11px;
    font-weight: 600;
    text-align: center;
    border: 1px solid rgba(90, 110, 220, 0.18);
    border-radius: 9px;
    min-height: 18px;
    max-height: 18px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #7b6aff, stop:0.6 #6a8bff, stop:1 #52a2ff);
    border-radius: 8px;
}

/* Active rendering — glow border */
QProgressBar[renderActive="true"] {
    border: 1px solid rgba(120, 145, 255, 0.40);
}

/* ===================================================================
   MESSAGE BOX — dark-themed dialogs
   =================================================================== */

QMessageBox {
    background-color: #0c1430;
    color: #dce4ff;
}

QMessageBox QLabel {
    color: #dce4ff;
    font-size: 13px;
}

QMessageBox QPushButton {
    background-color: rgba(22, 35, 72, 0.92);
    color: #d0dcff;
    border: 1px solid rgba(100, 120, 255, 0.20);
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 13px;
    min-width: 80px;
}

QMessageBox QPushButton:hover {
    background-color: rgba(32, 48, 92, 0.96);
    border: 1px solid rgba(120, 145, 255, 0.32);
}

/* ===================================================================
   TOOLTIPS
   =================================================================== */

QToolTip {
    background-color: rgba(16, 24, 52, 0.96);
    color: #d0dcff;
    border: 1px solid rgba(100, 120, 255, 0.22);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

/* ===================================================================
   PREVIEW
   =================================================================== */

QLabel#previewImage {
    background-color: rgba(5, 8, 18, 0.85);
    color: rgba(150, 168, 220, 0.55);
    border: 1px dashed rgba(100, 120, 255, 0.18);
    border-radius: 12px;
    font-size: 13px;
}

QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: rgba(70, 90, 170, 0.30);
}

QSlider::sub-page:horizontal {
    height: 6px;
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #4d6bff, stop:1 #7d9bff);
}

QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: #dce4ff;
    border: 2px solid #4d6bff;
}

QSlider::handle:horizontal:hover {
    background: #ffffff;
}
"""
