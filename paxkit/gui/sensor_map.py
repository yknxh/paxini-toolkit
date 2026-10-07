"""Sensor dot drawing (pyqtgraph): gauge-test map (the live heatmap is `taxel_heatmap.py`).

Test points: a left click emits `clicked(x, y)` in sensor mm (the Test tab turns it into a point); `show_points` draws them.

Dot positions come from the PXSR 3D view's point model (`device/geometry`), seen from above (up = rounded tip, right = +x).
Same orientation as the PXSR initial view: camera (0, 0, h) → origin, up = +y, no point rotation (`av0` 4551788).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from ..device.geometry import Geometry, load_geometry
from . import theme



class SensorMap(pg.PlotWidget):
    clicked = Signal(float, float)   # left click, sensor mm (top view)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAspectLocked(True)
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.setMouseEnabled(False, False)
        self.setMenuEnabled(False)
        self.hideButtons()
        self.geometry_: Optional[Geometry] = None
        self.model = ""
        self.surface = pg.ScatterPlotItem(size=3, pen=None, brush=pg.mkBrush(theme.MUTED))
        self.taxels = pg.ScatterPlotItem(size=9, pen=pg.mkPen(theme.BG, width=0.5))
        self.addItem(self.surface)
        self.addItem(self.taxels)
        self.empty = pg.TextItem("No sensor drawing", color=theme.MUTED, anchor=(0.5, 0.5))
        self.pts = pg.ScatterPlotItem(size=13, pen=pg.mkPen(theme.FG, width=1))
        self.cur = pg.ScatterPlotItem(size=24, pen=pg.mkPen(theme.ACCENT, width=3), brush=pg.mkBrush(0, 0, 0, 0))
        self.addItem(self.pts)
        self.addItem(self.cur)
        self.pt_texts: List[pg.TextItem] = []
        self.scene().sigMouseClicked.connect(self._on_click)

    def _on_click(self, ev) -> None:
        if ev.button() != Qt.LeftButton or self.geometry_ is None:
            return
        p = self.getViewBox().mapSceneToView(ev.scenePos())
        self.clicked.emit(float(p.x()), float(p.y()))

    def set_model(self, model: str) -> bool:
        """Load and draw the point model of a sensor model. False if there is none."""
        if model == self.model:
            return self.geometry_ is not None
        self.model = model
        self.geometry_ = load_geometry(model) if model else None
        self.show_points([])
        g = self.geometry_
        if g is None:
            self.surface.setData([], [])
            self.taxels.setData([], [])
            if self.empty.scene() is None:
                self.addItem(self.empty)
            return False
        if self.empty.scene() is not None:
            self.removeItem(self.empty)
        self.surface.setData(g.surface[:, 0], g.surface[:, 1], brush=pg.mkBrush(theme.MUTED))
        self.taxels.setData(g.taxels[:, 0], g.taxels[:, 1], brush=pg.mkBrush("#5a5c62"))
        self._fit()
        return True

    def _fit(self) -> None:
        """Fit the whole sensor in view (aspect kept). Refit on widget resize too (otherwise it looks clipped)."""
        g = getattr(self, "geometry_", None)   # resizeEvent arrives even while PlotWidget is being constructed
        if g is None:
            return
        pad = 1.5
        lo, hi = g.surface.min(axis=0), g.surface.max(axis=0)
        self.getViewBox().setRange(xRange=(lo[0] - pad, hi[0] + pad), yRange=(lo[1] - pad, hi[1] + pad), padding=0)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._fit()

    # ── test points ──
    def show_points(self, items: Sequence[Tuple[float, float, str, str]], current: Optional[Tuple[float, float]] = None) -> None:
        """items = (x mm, y mm, text, color). current = position of the selected point (ring), None = none."""
        self.pts.setData([i[0] for i in items], [i[1] for i in items], brush=[pg.mkBrush(i[3]) for i in items])
        self.cur.setData(*(([current[0]], [current[1]]) if current is not None else ([], [])))
        while len(self.pt_texts) > len(items):
            self.removeItem(self.pt_texts.pop())
        while len(self.pt_texts) < len(items):
            t = pg.TextItem("", color=theme.FG, anchor=(0, 1), fill=pg.mkBrush(QColor(30, 31, 34, 170)))
            self.addItem(t)
            self.pt_texts.append(t)
        for t, i in zip(self.pt_texts, items):
            t.setText(i[2])
            t.setPos(i[0] + 0.3, i[1] + 0.3)
