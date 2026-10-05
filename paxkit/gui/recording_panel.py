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

class RecordingPanel(QGroupBox):
    def __init__(self, directory: Optional[Path] = None) -> None:
        super().__init__("데이터 로깅")
        self.directory = directory
        self.sensor = None
        self.sensor_info: dict = {}
        self.recorder: Optional[CsvRecorder] = None
        self.stop_note = ""   # 기록이 저절로 멈춘 이유 (수신 멈춤 등)

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

    def on_stalled(self) -> Optional[Path]:
        """센서 수신 멈춤으로 리더가 기록을 멈췄으면 그 파일 (행이 없어 파일이 없으면 None)."""
        rec = self.recorder
        if rec is None or rec.active or not any(e["kind"] == "stalled" for e in rec.events):
            return None
        self.stop_note = "수신 멈춤으로 정지 (멈춘 시점까지 저장)"
        self._refresh()
        return rec.path if rec.file_exists else None

    def note_calibration(self, result) -> None:
        """캘리브레이션 탭에서 1회가 끝나면 (CalibrationResult). 기록 중이면 사이드카 `events`에 남긴다."""
        rec = self.recorder
        if rec is not None and rec.active:
            d = result.to_dict()
            d.pop("requested")
            rec.note_event("calibration", result.requested, **d)

    # ── 버튼 ──
    def _toggle(self) -> None:
        if self.recorder is not None and self.recorder.active:
            self.stop()
        elif self.sensor is not None:
            self.stop_note = ""
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
        note = f"\n{self.stop_note}" if self.stop_note and not recording else ""
        self.lbl_file.setText(name + note + (f"\n쓰기 오류: {rec.error}" if rec.error else ""))
        end = time.time() if recording else (rec.stopped_at or time.time())
        self.lbl_time.setText(f"{int(end - rec.started_at)} s")   # PXSR: 1초마다 +1
        self.lbl_rows.setText(str(rec.line_count))
