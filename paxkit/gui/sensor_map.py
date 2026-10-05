"""센서 점 그림 (pyqtgraph): 라이브 taxel 히트맵, 게이지 테스트 커버리지 지도.

점 위치는 PXSR 3D 화면의 점 모델(`device/geometry`), 위에서 본 모습 (위 = 둥근 끝, 오른쪽 = +x).
히트맵은 PXSR처럼 표면 점 = 이웃 taxel 값 × 가중치 합으로 칠한다. 색 범위는 화면 표시용이다.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor

from ..bench.zones import ZoneSet, load_zones
from ..device.geometry import Geometry, load_geometry
from . import theme

HEAT_MIN_SCALE = 20.0   # 히트맵 색 최대값의 하한 (taxel Z raw). 색 최대 = max(이 값, 지금 가장 큰 taxel)


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
        self.scale = HEAT_MIN_SCALE
        self.surface = pg.ScatterPlotItem(size=3, pen=None, brush=pg.mkBrush(theme.MUTED))
        self.taxels = pg.ScatterPlotItem(size=9, pen=pg.mkPen(theme.BG, width=0.5))
        self.addItem(self.surface)
        self.addItem(self.taxels)
        self.texts: List[pg.TextItem] = []
        self.cmap = pg.colormap.get("inferno")
        self.empty = pg.TextItem("센서 그림 없음", color=theme.MUTED, anchor=(0.5, 0.5))

    def set_model(self, model: str) -> bool:
        """센서 모델 점 모델을 읽어 그린다. 없으면 False."""
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
        pad = 1.5
        lo, hi = g.surface.min(axis=0), g.surface.max(axis=0)
        self.setXRange(lo[0] - pad, hi[0] + pad, padding=0)
        self.setYRange(lo[1] - pad, hi[1] + pad, padding=0)
        return True

    # ── 라이브 히트맵 ──
    def show_values(self, taxel_z: Optional[Sequence[float]]) -> None:
        g = self.geometry_
        if g is None:
            return
        if taxel_z is None or len(taxel_z) != g.n_taxels:
            self.surface.setBrush(pg.mkBrush(theme.MUTED))
            return
        v = np.clip(np.nan_to_num(np.asarray(taxel_z, dtype=float)), 0, None)
        scale = max(HEAT_MIN_SCALE, float(v.max()))
        sv = np.clip(g.surface_values(v) / scale, 0, 1)
        tv = np.clip(v / scale, 0, 1)
        self.scale = scale
        self.surface.setBrush(self.cmap.map(0.15 + 0.85 * sv, mode="qcolor"))
        self.taxels.setBrush(self.cmap.map(0.15 + 0.85 * tv, mode="qcolor"))

    # ── 커버리지 지도 ──
    def show_zones(self, colors: Sequence[str], labels: Sequence[str]) -> None:
        """구역마다 taxel 색과 글자 (구역 순서 = zones json 순서)."""
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
