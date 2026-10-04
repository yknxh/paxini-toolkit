"""메인 창. 지금은 빈 골격이고, 각 단계가 끝날 때마다 탭을 추가한다 (계획 P7)."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget

from .. import __version__
from ..config import Config
from ..paths import DATA_DIR


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"paxkit {__version__}")
        self.resize(1200, 800)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.statusBar().addWidget(QLabel(f"데이터 폴더: {DATA_DIR}"))
