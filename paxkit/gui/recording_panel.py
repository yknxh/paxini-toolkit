"""Data logging panel: PXSR record start/stop button (`K0`/`G1`). Files go to `data/logs/` as the same CSV as PXSR."""
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
        super().__init__("Data logging")
        self.directory = directory
        self.sensor = None
        self.sensor_info: dict = {}
        self.recorder: Optional[CsvRecorder] = None
        self.stop_note = ""   # why recording stopped on its own (data stall etc.)

        self.start_btn = QPushButton("Start recording")
        self.start_btn.clicked.connect(self._toggle)
        self.start_btn.setEnabled(False)
        folder_btn = QPushButton("Open folder")
        folder_btn.clicked.connect(self._open_folder)
        row = QHBoxLayout()
        row.addWidget(self.start_btn, 1)
        row.addWidget(folder_btn)

        self.lbl_file = QLabel("-")
        self.lbl_file.setWordWrap(True)
        self.lbl_time = QLabel("-")
        self.lbl_rows = QLabel("-")
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("Saved to the sidecar (.json); does not affect the CSV")
        form = QFormLayout(self)
        form.addRow(row)
        form.addRow("File", self.lbl_file)
        form.addRow("Elapsed", self.lbl_time)
        form.addRow("Rows", self.lbl_rows)
        form.addRow("Memo", self.memo)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(250)

    # ── from the device panel ──
    def set_sensor(self, sensor, info: Optional[dict] = None) -> None:
        """Sensor when connected, None when disconnected. The reader has already stopped recording when disconnect starts (PXSR `s1` → `G1`)."""
        self.sensor = sensor
        self.sensor_info = dict(info or {})
        self._refresh()

    def on_stalled(self) -> Optional[Path]:
        """The file, if the reader stopped recording because sensor data stalled (None if there were no rows and so no file)."""
        rec = self.recorder
        if rec is None or rec.active or not any(e["kind"] == "stalled" for e in rec.events):
            return None
        self.stop_note = "Data stalled — stopped (saved up to the stall)"
        self._refresh()
        return rec.path if rec.file_exists else None

    def note_calibration(self, result) -> None:
        """When one calibration run finishes in the Calibration tab (CalibrationResult). Logged to the sidecar `events` while recording."""
        rec = self.recorder
        if rec is not None and rec.active:
            d = result.to_dict()
            d.pop("requested")
            rec.note_event("calibration", result.requested, **d)

    # ── buttons ──
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

    # ── display ──
    def _refresh(self) -> None:
        rec = self.recorder
        recording = rec is not None and rec.active
        connected = self.sensor is not None and self.sensor.status == "connected" and self.sensor.is_alive()
        self.start_btn.setText("Stop recording" if recording else "Start recording")
        self.start_btn.setEnabled(recording or connected)
        if rec is None:
            return
        if recording:
            rec.memo = self.memo.text()
        name = rec.path.name if rec.path else "-"
        if not rec.file_exists:
            name += " (no rows yet)" if recording else " (no rows, file not created)"
        note = f"\n{self.stop_note}" if self.stop_note and not recording else ""
        self.lbl_file.setText(name + note + (f"\nWrite error: {rec.error}" if rec.error else ""))
        end = time.time() if recording else (rec.stopped_at or time.time())
        self.lbl_time.setText(f"{int(end - rec.started_at)} s")   # PXSR: +1 every second
        self.lbl_rows.setText(str(rec.line_count))
