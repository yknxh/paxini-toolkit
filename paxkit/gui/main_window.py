"""메인 창: 왼쪽 장치 패널 + 가운데 탭. 각 단계가 끝날 때마다 탭을 추가한다 (계획 P7)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter, QTabWidget, QVBoxLayout, QWidget

from .. import __version__
from ..config import Config
from ..paths import DATA_DIR
from .calibration_panel import CalibrationPanel
from .device_panel import DevicePanel
from .live_view import LiveView
from .recording_panel import RecordingPanel


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"paxkit {__version__}")
        self.resize(1200, 800)
        self.device = DevicePanel(cfg)
        self.live = LiveView()
        self.recording = RecordingPanel()
        self.calibration = CalibrationPanel()
        self.device.sensor_changed.connect(self.live.set_sensor)
        self.device.sensor_changed.connect(self._on_sensor)
        self.calibration.finished.connect(self.recording.note_calibration)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self.device, 1)
        left_lay.addWidget(self.recording)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.live, "라이브")
        self.tabs.addTab(self.calibration, "캘리브레이션")
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 880])
        self.setCentralWidget(split)
        self.statusBar().addWidget(QLabel(f"데이터 폴더: {DATA_DIR}"))

    def _on_sensor(self, s) -> None:
        info = self.device.connection_info() if s is not None else None
        self.recording.set_sensor(s, info)
        self.calibration.set_sensor(s, info)

    def closeEvent(self, ev) -> None:
        self.recording.stop()
        self.device.shutdown()
        super().closeEvent(ev)
