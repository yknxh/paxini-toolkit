"""센서 구역 (계획 P6-3): 모델마다 taxel 묶음 `zones/<label>.json` (초안은 `tools/make_zones.py`).

구역 판정: taxel Z(음수는 0)를 구역별로 더해 가장 큰 구역. 전체 합이 `min_taxel_sum`(raw)보다 작으면 위치 없음(-1).
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
    row: int        # 0 = 끝 (그림 위쪽)
    col: int        # 0 = 왼쪽
    taxels: Tuple[int, ...]


@dataclass(frozen=True)
class ZoneSet:
    model: str
    rule: str
    zones: Tuple[Zone, ...]
    geometry: Geometry
    member: np.ndarray      # (N, 구역 수) 0/1

    @property
    def names(self) -> List[str]:
        return [z.name for z in self.zones]

    def taxel_zone(self) -> np.ndarray:
        """taxel마다 속한 구역 번호 (없으면 -1)."""
        out = np.full(self.geometry.n_taxels, NO_ZONE)
        for k, z in enumerate(self.zones):
            out[list(z.taxels)] = k
        return out

    def zone_sums(self, taxel_z: np.ndarray) -> np.ndarray:
        """(n, N) taxel Z → (n, 구역 수) 구역별 합 (음수는 0)."""
        w = np.clip(np.atleast_2d(np.asarray(taxel_z, dtype=float)), 0, None)
        return w @ self.member

    def classify(self, taxel_z: np.ndarray, min_taxel_sum: float) -> np.ndarray:
        """(n, N) → (n,) 구역 번호. taxel Z 합 < min_taxel_sum 이거나 값이 없으면 -1."""
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
