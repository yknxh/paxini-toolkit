"""데이터 로깅 패널: PXSR 기록 시작/정지 버튼 (`K0`/`G1`). 파일은 `data/logs/`에 PXSR과 같은 CSV로 쓴다."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton

from ..paths import data_path
from ..recording import CsvRecorder

CALIBRATION_EVENTS = ("calibration_sent", "calibration_ack")


class RecordingPanel(QGroupBox):
    def __init__(self, directory: Optional[Path] = None) -> None:
        super().__init__("데이터 로깅")
        self.directory = directory
        self.sensor = None
        self.sensor_info: dict = {}
        self.recorder: Optional[CsvRecorder] = None

        self.start_btn = QPushButton("기록 시작")
        self.start_btn.clicked.connect(self._toggle)
        self.start_btn.setEnabled(False)
        folder_btn = QPushButton("폴더 열기")
        folder_btn.clicked.connect(self._open_folder)
        row = QHBoxLayout()
        row.addWidget(self.start_btn, 1)
        row.addWidget(folder_btn)

        self.lbl_file = QLabel("-")
        self.lbl_file.setWordWrap(True)
        self.lbl_time = QLabel("-")
        self.lbl_rows = QLabel("-")
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("사이드카(.json)에 저장, CSV에는 영향 없음")
        form = QFormLayout(self)
        form.addRow(row)
        form.addRow("파일", self.lbl_file)
        form.addRow("경과", self.lbl_time)
        form.addRow("행 수", self.lbl_rows)
        form.addRow("메모", self.memo)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(250)

    # ── 장치 패널에서 ──
    def set_sensor(self, sensor, info: Optional[dict] = None) -> None:
        """연결되면 센서, 해제되면 None. 해제 시작 때 기록은 리더가 이미 멈춘다 (PXSR `s1` → `G1`)."""
        self.sensor = sensor
        self.sensor_info = dict(info or {})
        self._refresh()

    def on_sensor_event(self, kind: str, info: dict) -> None:
        rec = self.recorder
        if rec is not None and rec.active and kind in CALIBRATION_EVENTS:
            extra = {k: v for k, v in info.items() if k != "t"}
            rec.note_event(kind, info.get("t"), **extra)

    # ── 버튼 ──
    def _toggle(self) -> None:
        if self.recorder is not None and self.recorder.active:
            self.stop()
        elif self.sensor is not None:
            self.recorder = CsvRecorder(self.sensor, self.directory, info=self.sensor_info)
            self.recorder.start()
            self._refresh()

    def stop(self) -> None:
        rec = self.recorder
        if rec is not None and rec.active:
            rec.memo = self.memo.text()
            rec.stop()
        self._refresh()

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory or data_path("logs"))))

    # ── 표시 ──
    def _refresh(self) -> None:
        rec = self.recorder
        recording = rec is not None and rec.active
        connected = self.sensor is not None and self.sensor.status == "connected" and self.sensor.is_alive()
        self.start_btn.setText("기록 정지" if recording else "기록 시작")
        self.start_btn.setEnabled(recording or connected)
        if rec is None:
            return
        if recording:
            rec.memo = self.memo.text()
        name = rec.path.name if rec.path else "-"
        if not rec.file_exists:
            name += " (첫 행 기록 전)" if recording else " (행 없음, 파일 안 만듦)"
        self.lbl_file.setText(name + (f"\n쓰기 오류: {rec.error}" if rec.error else ""))
        end = time.time() if recording else (rec.stopped_at or time.time())
        self.lbl_time.setText(f"{int(end - rec.started_at)} s")   # PXSR: 1초마다 +1
        self.lbl_rows.setText(str(rec.line_count))
