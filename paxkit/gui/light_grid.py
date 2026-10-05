"""Lightweight plot grid (instead of pyqtgraph `showGrid`).

With `showGrid` the axis (AxisItem) paints the grid as an image the size of the whole plot and repaints it whenever a curve changes.
On the live plot (25 Hz) that painting fought the receive thread for the GIL and USB reception dropped from 112 → 74 Hz
(simulation measurement; 105 Hz with only the grid off). Here the grid lines are a few `InfiniteLine`s,
moved only when the y-axis ticks change (the x time range is fixed, so x is set once).
"""
from __future__ import annotations

from typing import List

import pyqtgraph as pg

GRID_COLOR = "#34373c"   # faint line on the background (#1e1f22). Opaque instead of translucent (no blending)


class LightGrid:
    def __init__(self, plot: pg.PlotWidget, x_ticks=None) -> None:
        self.plot = plot
        self.pen = pg.mkPen(GRID_COLOR)
        self.y_lines: List[pg.InfiniteLine] = []
        for x in (x_ticks or ()):
            self._line(x, 90)
        self.vb = plot.getViewBox()
        self.vb.sigYRangeChanged.connect(self._update_y)
        self._update_y()

    def _line(self, pos: float, angle: int) -> pg.InfiniteLine:
        ln = pg.InfiniteLine(pos, angle=angle, pen=self.pen, movable=False)
        ln.setZValue(-100)
        self.plot.addItem(ln, ignoreBounds=True)
        return ln

    def _update_y(self, *_) -> None:
        lo, hi = self.vb.viewRange()[1]
        axis = self.plot.getAxis("left")
        size = max(axis.height(), 100)
        ticks = [v for _spacing, vals in axis.tickValues(lo, hi, size)[:1] for v in vals]   # major ticks only
        while len(self.y_lines) < len(ticks):
            self.y_lines.append(self._line(0, 0))
        for ln, v in zip(self.y_lines, ticks):
            ln.setPos(v)
            ln.show()
        for ln in self.y_lines[len(ticks):]:
            ln.hide()

