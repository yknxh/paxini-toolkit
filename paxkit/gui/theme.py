"""Dark theme (Qt Fusion style + dark palette, pyqtgraph background/foreground colors).

pyqtgraph options must be set before any plot widget is created, so this is called first when building the main window.
matplotlib result figures use the "dark" theme of `bench.plots` (same color values).
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#1e1f22"        # plot/input background
WINDOW = "#2b2d30"    # window background
PANEL = "#323438"     # buttons
FG = "#dcdcdc"
MUTED = "#8a8a8a"
ACCENT = "#3d7bd9"
OK = "#4caf50"
WARN = "#e0a030"
BAD = "#e05252"

STYLE = f"""
QGroupBox {{ border: 1px solid #45474c; border-radius: 4px; margin-top: 10px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {FG}; }}
QTabWidget::pane {{ border: 1px solid #45474c; }}
QTabBar::tab {{ background: {PANEL}; color: {MUTED}; padding: 6px 14px; border: 1px solid #45474c; border-bottom: none; }}
QTabBar::tab:selected {{ background: {WINDOW}; color: {FG}; }}
QPushButton {{ background: {PANEL}; border: 1px solid #4a4c52; border-radius: 3px; padding: 5px 10px; }}
QPushButton:hover {{ background: #3b3e43; }}
QPushButton:pressed {{ background: #2a2c30; }}
QPushButton:disabled {{ color: #6a6a6a; border-color: #3a3b3f; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton[primary="true"]:disabled {{ background: #2f4466; color: #9aa7bb; }}
QLineEdit, QComboBox, QListWidget, QTableWidget, QPlainTextEdit {{ background: {BG}; border: 1px solid #45474c; }}
QHeaderView::section {{ background: {PANEL}; color: {FG}; border: none; border-right: 1px solid #45474c; padding: 3px; }}
QToolTip {{ background: {PANEL}; color: {FG}; border: 1px solid #45474c; }}
"""


def apply(app: QApplication | None = None) -> None:
    """Apply the dark theme. Safe to call more than once."""
    pg.setConfigOptions(background=BG, foreground=FG, antialias=True)
    app = app or QApplication.instance()
    if app is None or app.property("paxkit_theme") == "dark":
        return
    app.setStyle("Fusion")
    p = QPalette()
    roles = {
        QPalette.Window: WINDOW, QPalette.WindowText: FG, QPalette.Base: BG, QPalette.AlternateBase: WINDOW,
        QPalette.ToolTipBase: PANEL, QPalette.ToolTipText: FG, QPalette.Text: FG, QPalette.Button: PANEL,
        QPalette.ButtonText: FG, QPalette.BrightText: "#ffffff", QPalette.Link: "#6aa6ff",
        QPalette.Highlight: ACCENT, QPalette.HighlightedText: "#ffffff", QPalette.PlaceholderText: MUTED,
    }
    for role, c in roles.items():
        p.setColor(role, QColor(c))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6a6a6a"))
    app.setPalette(p)
    app.setStyleSheet(STYLE)
    app.setProperty("paxkit_theme", "dark")
