"""라이브 탭: 합력 X/Y/Z 그래프 + taxel 히트맵. PXSR 화면처럼 raw / 10 = N 으로 표시한다 (`C0`, `l1=10`).

게이지가 연결되면 같은 그래프에 게이지 값(N)을 겹쳐 그린다. 센서·게이지 모두 같은 PC 시계로 수신 시각을 찍으므로
시각 보정 없이 그대로 겹친다. 지연은 상호상관으로 추정해 표시만 한다 (계획 P5).
게이지는 약 10 Hz라 점을 이으면 성기게 보여서, 각 값을 다음 값이 올 때까지(마지막 값은 지금까지) 유지하는 계단으로 그린다.
화면 표시만 그렇고 값을 만들어 넣지 않는다 (2026-10-05 사용자 결정).
히트맵은 마지막 프레임의 taxel Z를 센서 점 그림(PXSR 점 모델) 위에 칠한다 (`sensor_map.py`).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget

from ..device.clock import RealClock
from ..gauge import sensor_lag
from .sensor_map import SensorMap

WINDOW_S = 10.0
COLORS = ("#ff5c5c", "#5cd65c", "#5c9dff")
GAUGE_COLOR = "#f0f0f0"
LAG_EVERY = 25   # 지연 추정 주기 (그래프 갱신 횟수, 25 Hz → 1 s)
MAP_EVERY = 3    # 히트맵 갱신 주기 (그래프 갱신 횟수, 25 Hz → 약 8 Hz)


def latest_frame(sensor) -> Optional[object]:
    """리더의 `sensors`(채널 → 슬롯 → 마지막 프레임) 중 가장 최근 프레임."""
    best = None
    for row in list(getattr(sensor, "sensors", None) or []):
        for f in list(row or []):
            if f is not None and (best is None or f.t > best.t):
                best = f
    return best


def taxel_z(frame) -> Optional[list]:
    return None if frame is None else [v for v in frame.grid[2::3]]


def step_xy(x: np.ndarray, y: np.ndarray, x_end: float):
    """계단 좌표: y[i]를 x[i]부터 x[i+1]까지(마지막은 x_end까지) 유지한다."""
    xs = np.repeat(np.append(x, max(x_end, x[-1])), 2)[1:-1]
    ys = np.repeat(y, 2)
    return xs, ys


class LiveView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.sensor = None
        self.gauge = None
        self.clock = RealClock()
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "힘 (N)")
        self.plot.setLabel("bottom", "시간 (s)")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend()
        self.curves = [self.plot.plot(pen=pg.mkPen(c, width=2), name=n) for c, n in zip(COLORS, "XYZ")]
        self.gauge_curve = self.plot.plot(pen=pg.mkPen(GAUGE_COLOR, width=2), name="게이지")
        self.values = QLabel("-")
        self.gauge_value = QLabel("")
        self.lag = QLabel("")
        top = QHBoxLayout()
        top.addWidget(QLabel("최근 값:"))
        top.addWidget(self.values, 1)
        top.addWidget(self.gauge_value)
        top.addWidget(self.lag)
        self.map = SensorMap()
        self.map.setMinimumWidth(220)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.plot)
        split.addWidget(self.map)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([700, 260])
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        self.lag_value: Optional[float] = None   # 마지막 지연 추정 (s)
        self._n = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(40)   # 25 Hz

    def set_sensor(self, sensor: Optional[object]) -> None:
        if sensor is not None:
            self.sensor = sensor
        else:
            self.map.show_values(None)

    def set_gauge(self, gauge: Optional[object]) -> None:
        self.gauge = gauge
        if gauge is None:
            self.gauge_curve.setData([], [])
            self.gauge_value.setText("")
            self.lag.setText("")
            self.lag_value = None

    def _update(self) -> None:
        s, g = self.sensor, self.gauge
        if s is None and g is None:
            return
        now = self.clock.wall()
        if s is not None:
            t, v = s.buffer.snapshot(since=now - WINDOW_S)
            if t.size:
                x = t - now
                for i, c in enumerate(self.curves):
                    c.setData(x, v[:, i] / 10.0)
                self.values.setText("  ".join(f"{n} {val:6.1f} N" for n, val in zip("XYZ", v[-1] / 10.0)))
            if self._n % MAP_EVERY == 0:
                self._update_map(s)
        if g is not None:
            t, v = g.buffer.snapshot(since=now - WINDOW_S)
            if t.size:
                self.gauge_curve.setData(*step_xy(t - now, v[:, 0], 0.0))
                self.gauge_value.setText(f"게이지 {v[-1, 0]:6.1f} N")
            if s is not None and self._n % LAG_EVERY == 0:
                self._update_lag(now)
        self._n += 1
        self.plot.setXRange(-WINDOW_S, 0, padding=0)

    def _update_map(self, s) -> None:
        if self.map.set_model(s.sensor_type.label):
            self.map.show_values(taxel_z(latest_frame(s)))

    def _update_lag(self, now: float) -> None:
        lag, r = sensor_lag(self.gauge.buffer, self.sensor.buffer, now, window=WINDOW_S)
        self.lag_value = lag
        if lag is None:
            self.lag.setText("지연: - (눌러야 추정)")
        else:
            self.lag.setText(f"지연(센서−게이지): {lag * 1e3:+.0f} ms (r {r:.2f})")
