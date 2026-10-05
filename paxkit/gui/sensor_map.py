"""Sensor dot drawing (pyqtgraph): gauge-test coverage map (the live heatmap is `taxel_heatmap.py`).

Dot positions come from the PXSR 3D view's point model (`device/geometry`), seen from above (up = rounded tip, right = +x).
Same orientation as the PXSR initial view: camera (0, 0, h) → origin, up = +y, no point rotation (`av0` 4551788).
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor

from ..bench.zones import ZoneSet, load_zones
from ..device.geometry import Geometry, load_geometry
from . import theme



class SensorMap(pg.PlotWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAspectLocked(True)
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.setMouseEnabled(False, False)
        self.setMenuEnabled(False)
        self.hideButtons()
        self.geometry_: Optional[Geometry] = None
        self.zones: Optional[ZoneSet] = None
        self.model = ""
        self.surface = pg.ScatterPlotItem(size=3, pen=None, brush=pg.mkBrush(theme.MUTED))
        self.taxels = pg.ScatterPlotItem(size=9, pen=pg.mkPen(theme.BG, width=0.5))
        self.addItem(self.surface)
        self.addItem(self.taxels)
        self.texts: List[pg.TextItem] = []
        self.empty = pg.TextItem("No sensor drawing", color=theme.MUTED, anchor=(0.5, 0.5))

    def set_model(self, model: str) -> bool:
        """Load and draw the point model of a sensor model. False if there is none."""
        if model == self.model:
            return self.geometry_ is not None
        self.model = model
        self.geometry_ = load_geometry(model) if model else None
        self.zones = load_zones(model) if model else None
        for t in self.texts:
            self.removeItem(t)
        self.texts = []
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

    # ── coverage map ──
    def clear_zones(self) -> None:
        """Back to the plain drawing (gray taxels, no zone text): a previous test's coverage must not linger."""
        if self.geometry_ is not None:
            self.taxels.setBrush(pg.mkBrush("#5a5c62"))
        for t in self.texts:
            t.setText("")

    def show_zones(self, colors: Sequence[str], labels: Sequence[str]) -> None:
        """Taxel color and text per zone (zone order = zones json order)."""
        zs = self.zones
        if zs is None:
            return
        tz = zs.taxel_zone()
        self.taxels.setBrush([pg.mkBrush(colors[k] if k >= 0 else "#5a5c62") for k in tz])
        if not self.texts:
            for z in zs.zones:
                t = pg.TextItem("", color=theme.FG, anchor=(0.5, 0.5), fill=pg.mkBrush(QColor(30, 31, 34, 170)))
                p = zs.geometry.taxels[list(z.taxels)].mean(axis=0) if z.taxels else np.zeros(3)
                t.setPos(p[0], p[1])
                self.addItem(t)
                self.texts.append(t)
        for t, s in zip(self.texts, labels):
            t.setText(s)
