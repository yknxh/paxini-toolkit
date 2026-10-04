"""게이지 패널: 포트 선택, 연결/해제, 상태·수신 속도·최근 값 (계획 P5·P7).

포트를 열지 못하거나 끊기면 리더가 2초마다 다시 연다 (paxtest와 같음). 해제 버튼을 눌러야 멈춘다.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton

from ..config import Config
from ..device.transport import list_serial_ports
from ..gauge import GaugeBase, SerialGauge, SimGauge

SIM_GAUGE = "시뮬레이션 게이지"


class GaugePanel(QGroupBox):
    gauge_changed = Signal(object)   # GaugeBase 또는 None

    def __init__(self, cfg: Config) -> None:
        super().__init__("Force gauge")
        self.cfg = cfg
        self.gauge: Optional[GaugeBase] = None

        self.port_combo = QComboBox()
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(refresh)
        self.connect_btn = QPushButton("연결")
        self.connect_btn.clicked.connect(self._toggle)
        self.lbl_status = QLabel("연결 안 됨")
        self.lbl_status.setWordWrap(True)
        self.lbl_rate = QLabel("-")
        self.lbl_value = QLabel("-")
        form = QFormLayout(self)
        form.addRow("포트", port_row)
        form.addRow(self.connect_btn)
        form.addRow("상태", self.lbl_status)
        form.addRow("수신", self.lbl_rate)
        form.addRow("값", self.lbl_value)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(250)
        self.refresh_ports()

    def refresh_ports(self) -> None:
        cur = self.port_combo.currentData()
        self.port_combo.clear()
        for p in list_serial_ports():
            self.port_combo.addItem(f"{p.device} — {p.description}", p.device)
        self.port_combo.addItem(SIM_GAUGE, SIM_GAUGE)
        want = cur or self.cfg.get("gauge.port") or ""
        i = self.port_combo.findData(want) if want else -1
        self.port_combo.setCurrentIndex(i if i >= 0 else 0)

    def _toggle(self) -> None:
        if self.gauge is None:
            self.connect_gauge()
        else:
            self.disconnect_gauge()

    def connect_gauge(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            return
        if port == SIM_GAUGE:
            g: GaugeBase = SimGauge(rate_hz=10.0)   # 실측 게이지 스트림과 같은 약 10 Hz
        else:
            g = SerialGauge({**self.cfg.section("gauge"), "port": port})
        self.gauge = g
        g.start()
        self.connect_btn.setText("연결 해제")
        self.port_combo.setEnabled(False)
        self.gauge_changed.emit(g)

    def disconnect_gauge(self, timeout: float = 3.0) -> None:
        g = self.gauge
        if g is None:
            return
        g.stop()
        g.join(timeout)
        self.gauge = None
        self.connect_btn.setText("연결")
        self.port_combo.setEnabled(True)
        self.lbl_status.setText("연결 안 됨")
        self.lbl_rate.setText("-")
        self.gauge_changed.emit(None)

    def info(self) -> dict:
        g = self.gauge
        return {} if g is None else {"port": g.port, "simulated": isinstance(g, SimGauge)}

    def _refresh_status(self) -> None:
        g = self.gauge
        if g is None:
            return
        now = g.clock.wall()
        if g.status == "connected" and not g.receiving():
            self.lbl_status.setText(f"{g.port} 열림 · 값 없음 (게이지 전원·출력 설정·케이블 확인)")
        else:
            self.lbl_status.setText({"connected": f"연결됨 ({g.port})",
                                     "error": f"오류 (2초마다 재시도): {g.error}"}.get(g.status, g.status))
        self.lbl_rate.setText(f"{g.buffer.rate(now):.0f} Hz")
        v = g.latest()
        self.lbl_value.setText("-" if v is None else f"{v:.1f} N")
