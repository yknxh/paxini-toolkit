"""Live tab: resultant X/Y/Z plot + taxel heatmap. Shown as raw / 10 = N, like the PXSR view (`C0`, `l1=10`).

When a gauge is connected, its value (N) is overlaid on the same plot. Sensor and gauge are both timestamped on receipt with the same PC clock,
so they overlay as-is without time correction. Lag is estimated by cross-correlation and only displayed (plan P5).
The gauge runs at about 10 Hz, so joined points look sparse; it is drawn as steps holding each value until the next arrives (the last one until now).
This is display only; no values are synthesized (2026-10-05 user decision).
The heatmap (optional, "Heatmap" checkbox) paints the last frame's taxel Z on the sensor drawing (`taxel_heatmap.py`). When off, it is not updated.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..device.clock import RealClock
from ..gauge import sensor_lag
from .light_grid import LightGrid
from ..state import load_state, save_state
from .taxel_heatmap import TaxelHeatmap

WINDOW_S = 10.0
COLORS = ("#ff5c5c", "#5cd65c", "#5c9dff")
GAUGE_COLOR = "#f0f0f0"
LAG_EVERY = 25   # lag estimation period (plot updates, 25 Hz → 1 s)
MAP_EVERY = 3    # heatmap update period (plot updates, 25 Hz → about 8 Hz)


def latest_frame(sensor) -> Optional[object]:
    """Most recent frame among the reader's `sensors` (channel → slot → last frame)."""
    best = None
    for row in list(getattr(sensor, "sensors", None) or []):
        for f in list(row or []):
            if f is not None and (best is None or f.t > best.t):
                best = f
    return best


def taxel_z(frame) -> Optional[list]:
    return None if frame is None else [v for v in frame.grid[2::3]]


def step_xy(x: np.ndarray, y: np.ndarray, x_end: float):
    """Step coordinates: hold y[i] from x[i] to x[i+1] (the last one to x_end)."""
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
        self.plot.setLabel("left", "Force (N)")
        self.plot.setLabel("bottom", "Time (s)")
        self.grid = LightGrid(self.plot, x_ticks=range(-int(WINDOW_S), 1))   # showGrid is slow (light_grid.py)
        self.plot.addLegend()
        # line width 1: with width 2, line drawing fought the receive thread for the GIL longer and reception dropped by about 7 Hz (simulation measurement)
        self.curves = [self.plot.plot(pen=pg.mkPen(c, width=1), name=n) for c, n in zip(COLORS, "XYZ")]
        self.gauge_curve = self.plot.plot(pen=pg.mkPen(GAUGE_COLOR, width=2), name="Gauge")
        self.values = QLabel("-")
        self.gauge_value = QLabel("")
        self.lag = QLabel("")
        top = QHBoxLayout()
        top.addWidget(QLabel("Latest:"))
        top.addWidget(self.values, 1)
        top.addWidget(self.gauge_value)
        top.addWidget(self.lag)
        self.map_check = QCheckBox("Heatmap")
        self.map_check.setToolTip("Show taxel Z as colors on the sensor drawing (display only; not drawn when off)")
        top.addWidget(self.map_check)
        self.map = TaxelHeatmap()
        body = QHBoxLayout()
        body.addWidget(self.plot, 1)
        body.addWidget(self.map, 0, Qt.AlignVCenter)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(body, 1)
        self.map_check.setChecked(bool(load_state().get("live_heatmap", False)))
        self.map.setVisible(self.map_check.isChecked())
        self.map_check.toggled.connect(self._toggle_map)
        self.lag_value: Optional[float] = None   # last lag estimate (s)
        self._n = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(40)   # 25 Hz

    def _toggle_map(self, on: bool) -> None:
        self.map.setVisible(on)
        save_state(live_heatmap=on)
        if on and self.sensor is not None:
            self._update_map(self.sensor)

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
            if self._n % MAP_EVERY == 0 and self.map_check.isChecked():
                self._update_map(s)
        if g is not None:
            t, v = g.buffer.snapshot(since=now - WINDOW_S)
            if t.size:
                self.gauge_curve.setData(*step_xy(t - now, v[:, 0], 0.0))
                self.gauge_value.setText(f"Gauge {v[-1, 0]:6.1f} N")
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
            self.lag.setText("Lag: - (press to estimate)")
        else:
            self.lag.setText(f"Residual lag (sensor−gauge): {lag * 1e3:+.0f} ms (r {r:.2f})")
