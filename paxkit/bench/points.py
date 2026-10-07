"""Test points chosen by the user (2026-10-06 user decision, the default way to test).

The tester clicks a spot on the sensor drawing, then presses that spot. Locating presses from taxel values was unreliable
(force sometimes shows on taxels that were not pressed), so each gauge sample is located at the point selected at that time.
Selections are `point_select` session events (meta.json): a sample belongs to the last point selected before it
(none before the first selection or after a deselect = click outside the sensor).

Error % of a group of samples = mean over samples of the error relative to the gauge (the reference):
|e| % = mean(100·|e| / gauge), bias % = mean(100·e / gauge) (2026-10-06 user decision). Only stable samples with
gauge ≥ `pct_min_N` are used: during fast force changes the difference is partly timing, not sensor error.
The error map interpolates the per-point |e| % linearly between points (Delaunay triangles of the tested points); nothing is
extrapolated outside them.

Zones (`zones/<model>.json`, 7 per model, kept from the abolished zone method): used only to group the test points in
`points.png` (2026-10-07 user request). A point belongs to the zone containing its `taxel` (the taxel under the clicked
spot), so taxel values are still never used for location.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..device.geometry import Geometry, model_label

SELECT_EVENT = "point_select"
SNAP_MM = 1.0      # a click farther than this from every surface point (top view) is outside the sensor
MERGE_MM = 1.0     # a click within this of an existing point selects that point
VISIBLE_MM = 1.0   # surface points within this of the highest nearby one are the ones seen from above
ZONES_DIR = Path(__file__).resolve().parent / "zones"


@dataclass(frozen=True)
class TestPoint:
    __test__ = False   # not a pytest test class
    id: str
    x_mm: float
    y_mm: float
    z_mm: float
    taxel: int         # taxel with the largest weight under the point (for reference)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    row: int                  # position in the figure grid (row 0 = tip)
    col: int                  # 0 = left side, 1 = top, 2 = right side
    taxels: Tuple[int, ...]


@lru_cache(maxsize=None)
def load_zones(model: str) -> Tuple[Zone, ...]:
    """Zones of a sensor model (S1813E, S2015). Empty for an unknown model."""
    path = ZONES_DIR / f"{model_label(model)}.json"
    if not path.is_file():
        return ()
    d = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Zone(z["id"], z["name"], int(z["row"]), int(z["col"]), tuple(int(i) for i in z["taxels"]))
                 for z in d["zones"])


def zone_of(zones: Sequence[Zone], point: "TestPoint") -> Optional[Zone]:
    """Zone containing the point's taxel (None if no zone has it)."""
    return next((z for z in zones if point.taxel in z.taxels), None)


def snap(g: Geometry, x: float, y: float) -> Optional[Tuple[float, int]]:
    """Top-view click → (surface z mm, dominant taxel) of the surface seen from above at (x, y). None if outside the sensor."""
    d = np.hypot(g.surface[:, 0] - x, g.surface[:, 1] - y)
    cand = np.flatnonzero(d <= SNAP_MM)
    if not len(cand):
        return None
    z = g.surface[cand, 2]
    cand = cand[z >= z.max() - VISIBLE_MM]   # the top surface, not the side wall below the same spot
    i = int(cand[np.argmin(d[cand])])
    return float(g.surface[i, 2]), int(g.neighbor[i][np.argmax(g.shape[i])])


class PointSet:
    """Points of one test (GUI/CLI): ids P1, P2, ... in order of creation."""

    def __init__(self, geometry: Geometry) -> None:
        self.geometry = geometry
        self.points: List[TestPoint] = []

    def get(self, pid: Optional[str]) -> Optional[TestPoint]:
        return next((p for p in self.points if p.id == pid), None)

    def pick(self, x: float, y: float) -> Optional[TestPoint]:
        """Existing point within MERGE_MM, else a new point. None if (x, y) is outside the sensor."""
        if self.points:
            d = [np.hypot(p.x_mm - x, p.y_mm - y) for p in self.points]
            k = int(np.argmin(d))
            if d[k] <= MERGE_MM:
                return self.points[k]
        s = snap(self.geometry, x, y)
        if s is None:
            return None
        p = TestPoint(f"P{len(self.points) + 1}", round(float(x), 2), round(float(y), 2), round(s[0], 2), s[1])
        self.points.append(p)
        return p


def select_event(p: Optional[TestPoint]) -> Dict[str, Any]:
    """Event info for selecting p (None = deselect)."""
    return {"point": None} if p is None else {"point": p.id, "x_mm": p.x_mm, "y_mm": p.y_mm, "z_mm": p.z_mm,
                                               "taxel": p.taxel}


def selections(events: Iterable[Dict[str, Any]]) -> Tuple[np.ndarray, List[str]]:
    """`point_select` events → (times, point ids sorted by time; "" = none selected)."""
    ev = sorted((e for e in events if e.get("kind") == SELECT_EVENT), key=lambda e: e.get("t_unix_s", 0.0))
    return np.array([float(e.get("t_unix_s", 0.0)) for e in ev]), [e.get("point") or "" for e in ev]


def points_from_events(events: Iterable[Dict[str, Any]]) -> List[TestPoint]:
    """Points in order of first selection."""
    out: Dict[str, TestPoint] = {}
    for e in sorted((e for e in events if e.get("kind") == SELECT_EVENT), key=lambda e: e.get("t_unix_s", 0.0)):
        pid = e.get("point")
        if pid and pid not in out:
            out[pid] = TestPoint(pid, float(e["x_mm"]), float(e["y_mm"]), float(e.get("z_mm", 0.0)),
                                 int(e.get("taxel", -1)))
    return list(out.values())


def assign(t: np.ndarray, sel_t: np.ndarray, sel_ids: Sequence[str]) -> np.ndarray:
    """Point id of each sample time ("" if none selected)."""
    t = np.asarray(t, dtype=float)
    out = np.full(len(t), "", dtype=object)
    if not len(sel_t):
        return out
    k = np.searchsorted(sel_t, t, side="right") - 1
    ids = np.asarray(list(sel_ids), dtype=object)
    out[k >= 0] = ids[k[k >= 0]]
    return out


def error_pct(gauge: np.ndarray, err: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    """(bias %, |e| %) = mean(100·e / gauge), mean(100·|e| / gauge) over the samples (gauge > 0). None if there are none."""
    g = np.asarray(gauge, dtype=float)
    e = np.asarray(err, dtype=float)
    ok = np.isfinite(g) & np.isfinite(e) & (g > 0)
    if not ok.any():
        return None, None
    r = 100.0 * e[ok] / g[ok]
    return float(r.mean()), float(np.abs(r).mean())


def triangulation(x: np.ndarray, y: np.ndarray):
    """Delaunay triangulation of the tested points, or None (fewer than 3 points or all in a line)."""
    from matplotlib.tri import Triangulation

    if len(x) < 3:
        return None
    try:
        tri = Triangulation(x, y)
    except (RuntimeError, ValueError):
        return None
    return tri if len(tri.triangles) else None


def usable_summary(x: np.ndarray, y: np.ndarray, err: np.ndarray, k_pct: float, step_mm: float = 0.1) -> Dict[str, Any]:
    """Usable region at k: mean |e| % < k. Points that meet it, and the share of the tested area (inside the points'
    triangles, top view, value interpolated linearly) that does."""
    from matplotlib.tri import LinearTriInterpolator

    x, y, ae = (np.asarray(a, dtype=float) for a in (x, y, err))
    out: Dict[str, Any] = {"k_pct": float(k_pct), "points": int(len(ae)), "points_ok": int((ae < k_pct).sum()),
                           "area_mm2": None, "area_ok_mm2": None, "area_ok_pct": None}
    tri = triangulation(x, y)
    if tri is None:
        return out
    gx, gy = np.meshgrid(np.arange(x.min(), x.max() + step_mm, step_mm), np.arange(y.min(), y.max() + step_mm, step_mm))
    zg = LinearTriInterpolator(tri, ae)(gx, gy)
    inside = ~np.ma.getmaskarray(zg)
    ok = inside & (np.ma.filled(zg, np.inf) < k_pct)
    cell = step_mm * step_mm
    out.update(area_mm2=round(float(inside.sum() * cell), 2), area_ok_mm2=round(float(ok.sum() * cell), 2),
               area_ok_pct=round(100.0 * float(ok.sum()) / max(int(inside.sum()), 1), 2))
    return out
