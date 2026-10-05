"""Main window: device panels on the left + tabs in the center (Live / Calibration / Test / Results). Dark theme (plan P7)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox, QSplitter, QStyle, QSystemTrayIcon,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..bench import bench_settings
from ..config import Config
from ..device.sim import SimUsbTransport
from ..gauge import SimGauge
from ..paths import DATA_DIR
from . import theme
from .bench_panel import BenchPanel
from .bench_results import BenchResults
from .calibration_panel import CalibrationPanel
from .device_panel import DevicePanel
from .gauge_panel import GaugePanel
from .live_view import LiveView
from .recording_panel import RecordingPanel


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        theme.apply()   # pyqtgraph colors must be set before any plot widget is created
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"paxkit {__version__}")
        self.resize(1200, 800)
        self.device = DevicePanel(cfg)
        self.live = LiveView()
        self.recording = RecordingPanel()
        self.calibration = CalibrationPanel()
        self.gauge = GaugePanel(cfg)
        self.bench = BenchPanel(bench_settings(cfg), cfg.section("gauge"))
        self.results = BenchResults()
        self.device.sensor_changed.connect(self.live.set_sensor)
        self.device.sensor_changed.connect(self._on_sensor)
        self.device.sensor_event.connect(self._on_sensor_event)
        self.notice: Optional[QMessageBox] = None   # last notice box (checked in tests)
        self._tray: Optional[QSystemTrayIcon] = None
        self.calibration.finished.connect(self.recording.note_calibration)
        self.calibration.finished.connect(self.bench.note_calibration)
        self.gauge.gauge_changed.connect(self.live.set_gauge)
        self.gauge.gauge_changed.connect(self._on_gauge)
        self.bench.calibrate_requested.connect(self.calibration.calibrate)
        self.bench.calibration_busy = lambda: self.calibration.busy
        self.bench.session_saved.connect(lambda _f: self.results.refresh())
        self.bench.analyzed.connect(self._on_analyzed)
        self.bench.analysis_failed.connect(self._on_analysis_failed)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self.device, 1)
        left_lay.addWidget(self.gauge)
        left_lay.addWidget(self.recording)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.calibration, "Calibration")
        self.tabs.addTab(self.bench, "Test")
        self.tabs.addTab(self.results, "Results")
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 880])
        self.setCentralWidget(split)
        self.statusBar().addWidget(QLabel(f"Data folder: {DATA_DIR}"))

    def _on_sensor(self, s) -> None:
        info = self.device.connection_info() if s is not None else None
        self.recording.set_sensor(s, info)
        self.calibration.set_sensor(s, info)
        self.bench.set_sensor(s, info)
        self._link_sim()

    def _on_sensor_event(self, kind: str, info: dict) -> None:
        if kind != "stalled":
            return
        # The reader has already stopped the recordings (sinks), saved up to the stall. Clean up the UI + notify
        saved = [p for p in (self.recording.on_stalled(), self.bench.on_stalled(info)) if p is not None]
        last = info.get("t")
        when = datetime.fromtimestamp(last).strftime("%H:%M:%S.%f")[:-3] if last else "-"
        text = (f"Sensor data stalled (last frame {when}, no data for over {info.get('stall_s'):g} s).\n"
                "As in PXSR, data is not re-requested automatically. To continue, disconnect and reconnect.")
        if saved:
            text += "\n\nRecordings in progress were saved up to the stall and stopped:\n" + "\n".join(p.name for p in saved)
        self.notify("Sensor data stalled", text)

    def notify(self, title: str, text: str) -> None:
        """In-window notice (non-blocking message box) + taskbar flash + system notification (when a tray is available)."""
        self.statusBar().showMessage(f"{title}: {text.splitlines()[0]}", 60_000)
        box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, self)
        box.setWindowModality(Qt.NonModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()
        self.notice = box
        QApplication.alert(self)
        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._tray is None:
                self._tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_MessageBoxWarning), self)
            self._tray.show()
            self._tray.showMessage(title, text, QSystemTrayIcon.Warning, 15_000)

    def _on_gauge(self, g) -> None:
        self.bench.set_gauge(g, self.gauge.info() if g is not None else None)
        self._link_sim()

    def _on_analyzed(self, res) -> None:
        self.bench.analysis_done(res)
        self.results.show_result(res)
        self.tabs.setCurrentWidget(self.results)   # open the Results tab automatically after stopping

    def _on_analysis_failed(self, msg: str) -> None:
        self.bench.lbl_rec.setText(f"Analysis failed: {msg} (recorded files kept; re-analyze from the Results tab)")
        self.results.refresh()

    def _link_sim(self) -> None:
        """With a simulated gauge + simulated sensor, make both read the same load (for overlay and lag checks)."""
        g, s = self.gauge.gauge, self.device.sensor
        if isinstance(g, SimGauge):
            tr = getattr(s, "transport", None)
            g.load = tr.load_N if isinstance(tr, SimUsbTransport) else None

    def closeEvent(self, ev) -> None:
        if self._tray is not None:
            self._tray.hide()
        self.bench.stop_recording(cancel=True, reason="Recording stopped: window closed")
        self.recording.stop()
        self.gauge.disconnect_gauge()
        self.device.shutdown()
        super().closeEvent(ev)
