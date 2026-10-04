"""라이브 탭: 합력 X/Y/Z 그래프. PXSR 화면처럼 raw / 10 = N 으로 표시한다 (`C0`, `l1=10`)."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

WINDOW_S = 10.0
COLORS = ("#d62728", "#2ca02c", "#1f77b4")


class LiveView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.sensor = None
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "합력 (N)")
        self.plot.setLabel("bottom", "시간 (s)")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend()
        self.curves = [self.plot.plot(pen=pg.mkPen(c, width=2), name=n) for c, n in zip(COLORS, "XYZ")]
        self.values = QLabel("-")
        top = QHBoxLayout()
        top.addWidget(QLabel("최근 값:"))
        top.addWidget(self.values, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.plot, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(40)   # 25 Hz

    def set_sensor(self, sensor: Optional[object]) -> None:
        if sensor is not None:
            self.sensor = sensor

    def _update(self) -> None:
        s = self.sensor
        if s is None:
            return
        now = s.clock.wall()
        t, v = s.buffer.snapshot(since=now - WINDOW_S)
        if t.size == 0:
            return
        x = t - now
        for i, c in enumerate(self.curves):
            c.setData(x, v[:, i] / 10.0)
        last = v[-1] / 10.0
        self.values.setText("  ".join(f"{n} {val:6.1f} N" for n, val in zip("XYZ", last)))
        self.plot.setXRange(-WINDOW_S, 0, padding=0)
