"""메인 창: 왼쪽 장치 패널 + 가운데 탭 (라이브 / 캘리브레이션 / 테스트 / 결과). 어두운 테마 (계획 P7)."""
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
        theme.apply()   # pyqtgraph 색은 그래프 위젯을 만들기 전에 정해야 한다
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
        self.notice: Optional[QMessageBox] = None   # 마지막 알림 창 (테스트에서 확인)
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
        self.tabs.addTab(self.live, "라이브")
        self.tabs.addTab(self.calibration, "캘리브레이션")
        self.tabs.addTab(self.bench, "테스트")
        self.tabs.addTab(self.results, "결과")
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
        self.bench.set_sensor(s, info)
        self._link_sim()

    def _on_sensor_event(self, kind: str, info: dict) -> None:
        if kind != "stalled":
            return
        # 리더가 이미 기록(sink)을 멈췄다 (멈춘 시점까지 저장). 화면 쪽 정리 + 알림
        saved = [p for p in (self.recording.on_stalled(), self.bench.on_stalled(info)) if p is not None]
        last = info.get("t")
        when = datetime.fromtimestamp(last).strftime("%H:%M:%S.%f")[:-3] if last else "-"
        text = (f"센서 수신이 멈췄습니다 (마지막 프레임 {when}, {info.get('stall_s'):g}초 넘게 데이터 없음).\n"
                "PXSR과 같이 자동으로 다시 요청하지 않습니다. 계속하려면 연결 해제 후 다시 연결하세요.")
        if saved:
            text += "\n\n진행 중이던 기록은 멈춘 시점까지 저장하고 정지했습니다:\n" + "\n".join(p.name for p in saved)
        self.notify("센서 수신 멈춤", text)

    def notify(self, title: str, text: str) -> None:
        """창 안 알림(막지 않는 메시지 창) + 작업 표시줄 깜빡임 + 시스템 알림 (트레이를 쓸 수 있을 때)."""
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
        self.tabs.setCurrentWidget(self.results)   # 정지 후 결과 탭 자동으로 열기

    def _on_analysis_failed(self, msg: str) -> None:
        self.bench.lbl_rec.setText(f"분석 실패: {msg} (기록 파일은 남아 있음, 결과 탭에서 재분석)")
        self.results.refresh()

    def _link_sim(self) -> None:
        """시뮬레이션 게이지 + 시뮬레이션 센서면 같은 하중을 읽게 한다 (겹쳐 보기·지연 확인용)."""
        g, s = self.gauge.gauge, self.device.sensor
        if isinstance(g, SimGauge):
            tr = getattr(s, "transport", None)
            g.load = tr.load_N if isinstance(tr, SimUsbTransport) else None

    def closeEvent(self, ev) -> None:
        if self._tray is not None:
            self._tray.hide()
        self.bench.stop_recording(cancel=True, reason="창을 닫아 기록을 멈췄습니다")
        self.recording.stop()
        self.gauge.disconnect_gauge()
        self.device.shutdown()
        super().closeEvent(ev)
