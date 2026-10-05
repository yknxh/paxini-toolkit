"""Calibration tab: same action as the PXSR calibration button (`O3`) + result and before/after values (plan P7).

The PXSR button can be pressed before connecting, but then there is nowhere to send, and on the next connect `P1` clears the polling-stopped flag,
so the sensor-side result is the same. Here it can be pressed only while connected (2026-10-05 user decision).
Repeated clicks are blocked (2026-10-05 user decision; PXSR does not block them): the button stays locked until one run finishes
(reply + 0.5 s of after-values, or a no-reply verdict). The commands sent to the sensor per press are the same as one PXSR click.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..calibration import OUTCOME_TEXT, CalibrationResult, CalibrationRun, append_history, history_path

AXES = ("X", "Y", "Z")


def _ms(a: Optional[float], b: Optional[float]) -> str:
    return "-" if a is None or b is None else f"{(b - a) * 1e3:.0f} ms"


class CalibrationPanel(QWidget):
    finished = Signal(object)   # CalibrationResult

    def __init__(self, history_file=None) -> None:
        super().__init__()
        self.sensor = None
        self.sensor_info: dict = {}
        self.history_file = history_file
        self.runs: List[CalibrationRun] = []
        self.last: Optional[CalibrationResult] = None

        note = QLabel("Run with nothing pressing on the sensor. Sends the same command as the PXSR calibration button; "
                      "the sensor firmware sets the zero (the app does not correct or store values).")
        note.setWordWrap(True)
        self.cal_btn = QPushButton("Calibrate")
        self.cal_btn.setEnabled(False)
        self.cal_btn.clicked.connect(self.calibrate)
        open_btn = QPushButton("Open history file")
        open_btn.clicked.connect(self._open_history)
        row = QHBoxLayout()
        row.addWidget(self.cal_btn, 1)
        row.addWidget(open_btn)

        box = QGroupBox("Last result")
        form = QFormLayout(box)
        self.lbl_outcome = QLabel("-")
        self.lbl_outcome.setWordWrap(True)
        self.lbl_time = QLabel("-")
        self.lbl_delay = QLabel("-")
        form.addRow("Result", self.lbl_outcome)
        form.addRow("Requested at", self.lbl_time)
        form.addRow("Button→send→reply", self.lbl_delay)
        self.table = QTableWidget(2, 3)
        self.table.setHorizontalHeaderLabels([f"{a} (N)" for a in AXES])
        self.table.setVerticalHeaderLabels(["Before (0.5 s mean)", "After (0.5 s mean)"])
        self.table.setMaximumHeight(100)
        form.addRow("Resultant", self.table)

        self.history = QListWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(row)
        lay.addWidget(box)
        lay.addWidget(QLabel("Runs this session (also saved to data/calibration/history.jsonl)"))
        lay.addWidget(self.history, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)

    def set_sensor(self, sensor, info: Optional[dict] = None) -> None:
        self.sensor = sensor
        self.sensor_info = dict(info or {})
        self._update_button()

    @property
    def busy(self) -> bool:
        """A calibration run is in progress (repeated-click lock)."""
        return bool(self.runs)

    def calibrate(self) -> None:
        s = self.sensor
        if s is None or s.status != "connected" or not s.is_alive() or self.busy:
            return
        self.runs.append(CalibrationRun(s, self.sensor_info).start())
        self._update_button()
        self._show(self.runs[-1].result)

    def _poll(self) -> None:
        self._update_button()
        for run in list(self.runs):
            if run.poll():
                self.runs.remove(run)
                append_history(run.result, self.history_file)
                self.history.addItem(self._summary(run.result))
                self.history.scrollToBottom()
                self.finished.emit(run.result)
            self._show(run.result)

    def _update_button(self) -> None:
        s = self.sensor
        self.cal_btn.setEnabled(s is not None and s.status == "connected" and s.is_alive() and not self.busy)
        self.cal_btn.setText("Calibrating…" if self.busy else "Calibrate")

    def _summary(self, r: CalibrationResult) -> str:
        t = datetime.fromtimestamp(r.requested).strftime("%H:%M:%S")
        z = lambda v: "-" if v is None else f"{v[2] / 10:.1f}"
        return f"{t}  {r.info.get('sensor', '')}  {r.outcome}  Z {z(r.before)} → {z(r.after)} N"

    def _show(self, r: CalibrationResult) -> None:
        self.last = r
        self.lbl_outcome.setText(OUTCOME_TEXT.get(r.outcome, r.outcome)
                                 + ("" if r.status is None else f" — status {r.status}, function code {r.function_code}"))
        self.lbl_time.setText(datetime.fromtimestamp(r.requested).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
        self.lbl_delay.setText(f"{_ms(r.requested, r.sent)} / {_ms(r.sent, r.acked)}")
        for i, v in enumerate((r.before, r.after)):
            for j in range(3):
                self.table.setItem(i, j, QTableWidgetItem("-" if v is None else f"{v[j] / 10:.2f}"))

    def _open_history(self) -> None:
        p = self.history_file or history_path()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_file() else p.parent)))
