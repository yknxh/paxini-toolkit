"""Live-tab taxel heatmap (optional, toggled with the live tab "Heatmap" checkbox).

A display-only extra, so it is drawn lightly instead of matching the PXSR view (2026-10-06 user decision):
- QPainter instead of pyqtgraph, drawing only the taxel circles (S1813E 31, S2015 52). Surface points are drawn once as a gray outline and reused.
- 64 precomputed color levels; repaint only when a value's level changes.
- Small and centered in the area, keeping the sensor aspect (at most `MAX_W` × `MAX_H` px). Up = rounded tip (+y), right = +x (same orientation as the PXSR initial view).
Dot positions and taxel numbering match the PXSR point model (`device/geometry`).
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..device.geometry import Geometry, load_geometry
from . import theme

HEAT_MIN_SCALE = 20.0   # lower bound of the color maximum (taxel Z raw = 2 N). Color max = max(this, current largest taxel)
HEAT_LEVELS = 64
MAX_W, MAX_H = 170, 230   # maximum drawing size (px)
EMPTY = QColor("#3a3c42")


class TaxelHeatmap(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setMinimumSize(MAX_W // 2, MAX_H // 2)
        self.model = ""
        self.geom: Optional[Geometry] = None
        self.levels: Optional[np.ndarray] = None   # color level per taxel (None = gray)
        lut = pg.colormap.get("inferno").map(0.15 + 0.85 * np.linspace(0, 1, HEAT_LEVELS), mode="qcolor")
        self.colors = list(lut)
        self._bg: Optional[QPixmap] = None
        self._bg_key = None

    def sizeHint(self) -> QSize:
        return QSize(MAX_W + 20, MAX_H + 20)

    def set_model(self, model: str) -> bool:
        if model != self.model:
            self.model = model
            self.geom = load_geometry(model) if model else None
            self.levels = None
            self._bg = None
            self.update()
        return self.geom is not None

    def show_values(self, taxel_z: Optional[Sequence[float]]) -> None:
        g = self.geom
        if g is None:
            return
        if taxel_z is None or len(taxel_z) != g.n_taxels:
            k = None
        else:
            v = np.clip(np.nan_to_num(np.asarray(taxel_z, dtype=float)), 0, None)
            scale = max(HEAT_MIN_SCALE, float(v.max()))
            k = np.rint(v / scale * (HEAT_LEVELS - 1)).astype(int)
        if (k is None and self.levels is None) or (k is not None and self.levels is not None
                                                     and np.array_equal(k, self.levels)):
            return   # no repaint if nothing changed
        self.levels = k
        self.update()

    # ── painting ──
    def _layout(self):
        """mm → px transform (aspect kept, centered, size capped)."""
        g = self.geom
        lo, hi = g.surface[:, :2].min(axis=0) - 1.5, g.surface[:, :2].max(axis=0) + 1.5
        span = hi - lo
        s = min(min(self.width(), MAX_W) / span[0], min(self.height(), MAX_H) / span[1])
        cx, cy = self.width() / 2, self.height() / 2
        mid = (lo + hi) / 2
        return s, cx, cy, mid

    def _background(self, s, cx, cy, mid) -> QPixmap:
        key = (self.width(), self.height(), self.model)
        if self._bg is None or self._bg_key != key:
            pm = QPixmap(self.size())
            pm.fill(QColor(theme.BG))
            p = QPainter(pm)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.MUTED))
            for x, y in self.geom.surface[:, :2]:
                p.drawEllipse(QPointF(cx + (x - mid[0]) * s, cy - (y - mid[1]) * s), 0.8, 0.8)
            p.end()
            self._bg, self._bg_key = pm, key
        return self._bg

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self.geom is None:
            p.fillRect(self.rect(), QColor(theme.BG))
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "No sensor connected")
            return
        s, cx, cy, mid = self._layout()
        p.drawPixmap(0, 0, self._background(s, cx, cy, mid))
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        r = max(2.0, 0.6 * s)   # small: seen from above, side-face taxels are only ~1 mm apart
        k = self.levels
        for i, (x, y) in enumerate(self.geom.taxels[:, :2]):
            p.setBrush(EMPTY if k is None else self.colors[k[i]])
            p.drawEllipse(QRectF(cx + (x - mid[0]) * s - r, cy - (y - mid[1]) * s - r, 2 * r, 2 * r))
