"""메인 창: 왼쪽 장치 패널 + 가운데 탭. 각 단계가 끝날 때마다 탭을 추가한다 (계획 P7)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter, QTabWidget

from .. import __version__
from ..config import Config
from ..paths import DATA_DIR
from .device_panel import DevicePanel
from .live_view import LiveView


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"paxkit {__version__}")
        self.resize(1200, 800)
        self.device = DevicePanel(cfg)
        self.live = LiveView()
        self.device.sensor_changed.connect(self.live.set_sensor)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.live, "라이브")
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.device)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 880])
        self.setCentralWidget(split)
        self.statusBar().addWidget(QLabel(f"데이터 폴더: {DATA_DIR}"))

    def closeEvent(self, ev) -> None:
        self.device.shutdown()
        super().closeEvent(ev)
