"""캘리브레이션 탭: PXSR 캘리브레이션 버튼(`O3`)과 같은 동작 + 결과·전후 값 표시 (계획 P7).

PXSR 버튼은 연결 전에도 눌리지만, 연결 전에는 보낼 곳이 없고 다음 연결 때 `P1`이 폴링 중지 표시를 지우므로
센서 쪽 결과는 같다. 여기서는 연결됐을 때만 누를 수 있게 한다 (2026-10-05 사용자 결정).
중복 클릭은 막는다 (2026-10-05 사용자 결정, PXSR은 막지 않음): 1회가 끝날 때까지(응답 + 후 값 0.5 s, 또는 응답 없음 판정)
버튼을 잠근다. 한 번 누를 때 센서로 가는 명령은 PXSR 1회 클릭과 같다.
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

        note = QLabel("센서를 누르지 않은 상태에서 실행하세요. PXSR 캘리브레이션 버튼과 같은 명령을 보내고, "
                      "영점은 센서 펌웨어가 잡습니다 (앱은 값을 보정·저장하지 않음).")
        note.setWordWrap(True)
        self.cal_btn = QPushButton("캘리브레이션")
        self.cal_btn.setEnabled(False)
        self.cal_btn.clicked.connect(self.calibrate)
        open_btn = QPushButton("기록 파일 열기")
        open_btn.clicked.connect(self._open_history)
        row = QHBoxLayout()
        row.addWidget(self.cal_btn, 1)
        row.addWidget(open_btn)

        box = QGroupBox("마지막 결과")
        form = QFormLayout(box)
        self.lbl_outcome = QLabel("-")
        self.lbl_outcome.setWordWrap(True)
        self.lbl_time = QLabel("-")
        self.lbl_delay = QLabel("-")
        form.addRow("결과", self.lbl_outcome)
        form.addRow("요청 시각", self.lbl_time)
        form.addRow("버튼→전송→응답", self.lbl_delay)
        self.table = QTableWidget(2, 3)
        self.table.setHorizontalHeaderLabels([f"{a} (N)" for a in AXES])
        self.table.setVerticalHeaderLabels(["전 0.5 s 평균", "후 0.5 s 평균"])
        self.table.setMaximumHeight(100)
        form.addRow("합력", self.table)

        self.history = QListWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(row)
        lay.addWidget(box)
        lay.addWidget(QLabel("이번 실행 기록 (data/calibration/history.jsonl에도 저장)"))
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
        """캘리브레이션 1회가 진행 중 (중복 클릭 잠금)."""
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
        self.cal_btn.setText("캘리브레이션 중…" if self.busy else "캘리브레이션")

    def _summary(self, r: CalibrationResult) -> str:
        t = datetime.fromtimestamp(r.requested).strftime("%H:%M:%S")
        z = lambda v: "-" if v is None else f"{v[2] / 10:.1f}"
        return f"{t}  {r.info.get('sensor', '')}  {r.outcome}  Z {z(r.before)} → {z(r.after)} N"

    def _show(self, r: CalibrationResult) -> None:
        self.last = r
        self.lbl_outcome.setText(OUTCOME_TEXT.get(r.outcome, r.outcome)
                                 + ("" if r.status is None else f" — status {r.status}, 기능 코드 {r.function_code}"))
        self.lbl_time.setText(datetime.fromtimestamp(r.requested).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
        self.lbl_delay.setText(f"{_ms(r.requested, r.sent)} / {_ms(r.sent, r.acked)}")
        for i, v in enumerate((r.before, r.after)):
            for j in range(3):
                self.table.setItem(i, j, QTableWidgetItem("-" if v is None else f"{v[j] / 10:.2f}"))

    def _open_history(self) -> None:
        p = self.history_file or history_path()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_file() else p.parent)))
