"""장치 패널: 포트 선택, 연결/해제, 센서 상태. 캘리브레이션 버튼은 캘리브레이션 탭에 있다."""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QVBoxLayout, QWidget)

from ..config import Config
from ..device.sim import SIM_VERSIONS, SimUsbTransport
from ..device.transport import SerialTransport, list_serial_ports
from ..device.usb import STALL_S, UsbSensor
from ..state import load_state, save_state
from . import theme

SIM_PREFIX = "시뮬레이션: "


class DevicePanel(QWidget):
    sensor_changed = Signal(object)   # UsbSensor 또는 None
    sensor_event = Signal(str, dict)  # 리더 이벤트 (GUI 스레드에서)
    _event_sig = Signal(str, dict)    # 리더 스레드 → GUI 스레드

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.sensor: Optional[UsbSensor] = None
        self._t_connect = 0.0
        self._is_sim = False
        self._port = ""

        self.port_combo = QComboBox()
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(refresh)

        self.connect_btn = QPushButton("연결")
        self.connect_btn.clicked.connect(self._toggle)

        box = QGroupBox("센서 (USB 직결)")
        form = QFormLayout(box)
        self.lbl_status = QLabel("연결 안 됨")
        self.lbl_type = QLabel("-")
        self.lbl_version = QLabel("-")
        self.lbl_version.setWordWrap(True)
        self.lbl_rate = QLabel("-")
        form.addRow("포트", port_row)
        form.addRow(self.connect_btn)
        form.addRow("상태", self.lbl_status)
        form.addRow("타입", self.lbl_type)
        form.addRow("버전", self.lbl_version)
        form.addRow("수신", self.lbl_rate)

        self.events = QListWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addWidget(QLabel("이벤트"))
        lay.addWidget(self.events, 1)

        self._event_sig.connect(self._on_event_gui)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(250)
        self.refresh_ports()

    # ── 포트 ──
    def refresh_ports(self) -> None:
        cur = self.port_combo.currentData()
        self.port_combo.clear()
        want = cur or self.cfg.get("device.port") or ""
        sensor_idx = None
        for p in list_serial_ports():
            label = f"{p.device} — {p.description}" + ("  [센서 후보]" if p.is_sensor else "")
            self.port_combo.addItem(label, p.device)
            if p.is_sensor and sensor_idx is None:
                sensor_idx = self.port_combo.count() - 1
        for name in SIM_VERSIONS:
            self.port_combo.addItem(SIM_PREFIX + name, SIM_PREFIX + name)
        i = self.port_combo.findData(want) if want else -1
        self.port_combo.setCurrentIndex(i if i >= 0 else (sensor_idx or 0))

    # ── 연결 ──
    def _toggle(self) -> None:
        if self.sensor is None:
            self.connect_sensor()
        else:
            self.disconnect_sensor()

    def connect_sensor(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            return
        self._is_sim = port.startswith(SIM_PREFIX)
        self._port = port
        if self._is_sim:
            transport = SimUsbTransport(port[len(SIM_PREFIX):])
        else:
            transport = SerialTransport(port)
        self.events.clear()
        self.sensor = UsbSensor(transport, specification=load_state()["specification"],
                                on_event=lambda k, info: self._event_sig.emit(k, info),
                                stall_s=float(self.cfg.get("device.stall_s") or STALL_S))
        self._t_connect = time.monotonic()
        self.sensor.start()
        self.connect_btn.setText("연결 해제")
        self.port_combo.setEnabled(False)
        self.sensor_changed.emit(self.sensor)

    def connection_info(self) -> dict:
        """기록 사이드카에 남길 연결 정보."""
        return {"mode": "usb", "port": self._port, "simulated": self._is_sim}

    def disconnect_sensor(self) -> None:
        if self.sensor is not None and self.sensor.is_alive():
            self.sensor.disconnect()
            self.connect_btn.setEnabled(False)   # 해제 완료(약 0.9 s)까지

    def shutdown(self, timeout: float = 3.0) -> None:
        s = self.sensor
        if s is not None and s.is_alive():
            s.disconnect()
            s.join(timeout)

    # ── 상태 표시 ──
    def _on_event_gui(self, kind: str, info: dict) -> None:
        t = time.monotonic() - self._t_connect
        text = {
            "connected": "포트 열림",
            "version": f"버전 응답 serviceID {info.get('service_id')}: {info.get('version')}",
            "sensor_type": f"센서 타입 {info.get('sensor')} (taxel {info.get('taxels')})",
            "calibration_sent": "캘리브레이션 명령 전송",
            "calibration_ack": f"캘리브레이션 응답 (status {info.get('status')}"
                               + (", Setting.failed)" if info.get("failed") else ")"),
            "warning": f"경고: {info.get('message')}",
            "error": f"오류: {info.get('message')}",
            "disconnected": "연결 해제됨",
            "stalled": f"수신 멈춤 ({info.get('stall_s'):g} s 넘게 프레임 없음) — 기록 정지, 다시 연결 필요",
            "resumed": "수신 다시 시작 (기록은 멈춘 상태)",
        }.get(kind, f"{kind} {info}")
        self.events.addItem(f"[{t:6.2f}s] {text}")
        self.events.scrollToBottom()
        if kind == "sensor_type" and not self._is_sim:
            save_state(specification=info["sensor"])   # PXSR `K`: specification 저장
        self.sensor_event.emit(kind, info)

    def _refresh_status(self) -> None:
        s = self.sensor
        if s is None:
            return
        if s.status == "connected" and s.stalled:
            self.lbl_status.setText("<span style='color:%s'>수신 멈춤 — 연결 해제 후 다시 연결</span>" % theme.BAD)
        else:
            self.lbl_status.setText({"connected": "연결됨", "error": f"오류: {s.error}"}.get(s.status, s.status))
        self.lbl_type.setText(f"{s.sensor_type.label} (taxel {s.sensor_type.forces}), "
                              f"serviceID {s.service_id}, slot {s.slot}")
        self.lbl_version.setText(s.version or "-")
        rate = s.buffer.rate(s.clock.wall())
        self.lbl_rate.setText(f"{rate:.0f} Hz, 프레임 {s.frame_count}")
        if not s.is_alive():
            self.sensor = None
            self.connect_btn.setText("연결")
            self.connect_btn.setEnabled(True)
            self.port_combo.setEnabled(True)
            if s.status != "error":
                self.lbl_status.setText("연결 안 됨")
            self.sensor_changed.emit(None)
