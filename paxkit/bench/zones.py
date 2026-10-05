"""Sensor zones (plan P6-3): taxel groups per model in `zones/<label>.json` (draft from `tools/make_zones.py`).

Zone classification: sum taxel Z (negatives as 0) per zone and take the largest. If the total is below
`min_taxel_sum` (raw), there is no position (-1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from ..device.geometry import Geometry, load_geometry, model_label

ZONES_DIR = Path(__file__).resolve().parent / "zones"
NO_ZONE = -1


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    row: int        # 0 = tip (top of the figure)
    col: int        # 0 = left
    taxels: Tuple[int, ...]


@dataclass(frozen=True)
class ZoneSet:
    model: str
    rule: str
    zones: Tuple[Zone, ...]
    geometry: Geometry
    member: np.ndarray      # (N, n_zones) 0/1

    @property
    def names(self) -> List[str]:
        return [z.name for z in self.zones]

    def taxel_zone(self) -> np.ndarray:
        """Zone index of each taxel (-1 if none)."""
        out = np.full(self.geometry.n_taxels, NO_ZONE)
        for k, z in enumerate(self.zones):
            out[list(z.taxels)] = k
        return out

    def zone_sums(self, taxel_z: np.ndarray) -> np.ndarray:
        """(n, N) taxel Z → (n, n_zones) per-zone sums (negatives as 0)."""
        w = np.clip(np.atleast_2d(np.asarray(taxel_z, dtype=float)), 0, None)
        return w @ self.member

    def classify(self, taxel_z: np.ndarray, min_taxel_sum: float) -> np.ndarray:
        """(n, N) → (n,) zone index. -1 if the taxel Z sum < min_taxel_sum or values are missing."""
        tz = np.atleast_2d(np.asarray(taxel_z, dtype=float))
        bad = ~np.isfinite(tz).all(axis=1)
        tz = np.where(np.isfinite(tz), tz, 0.0)
        s = self.zone_sums(tz)
        k = s.argmax(axis=1)
        total = np.clip(tz, 0, None).sum(axis=1)
        k[(total < min_taxel_sum) | bad] = NO_ZONE
        return k


def has_zones(name: str) -> bool:
    return (ZONES_DIR / f"{model_label(name)}.json").is_file()


def load_zones(name: str) -> Optional[ZoneSet]:
    return _load(model_label(name))


@lru_cache(maxsize=None)
def _load(label: str) -> Optional[ZoneSet]:
    path = ZONES_DIR / f"{label}.json"
    g = load_geometry(label)
    if not path.is_file() or g is None:
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    zones = tuple(Zone(z["id"], z["name"], int(z["row"]), int(z["col"]), tuple(int(i) for i in z["taxels"]))
                  for z in d["zones"])
    member = np.zeros((g.n_taxels, len(zones)))
    for k, z in enumerate(zones):
        member[list(z.taxels), k] = 1.0
    return ZoneSet(model=label, rule=d.get("rule", ""), zones=zones, geometry=g, member=member)
