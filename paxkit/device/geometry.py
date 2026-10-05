"""Sensor point model (the `j4` map of the PXSR 3D view, extracted with `tools/extract_geometry.py`).

- `taxels`: position of taxel i (mm, PXSR sensor coordinates). Same values as the paxtest geometry JSON (same source).
- `surface`: surface point positions with 4 neighbor taxels and weights. PXSR colors a surface point by the sum of neighbor taxel value × weight.
- `normals`: taxel surface normals (third column of rotation matrix `B6`/`U6`). Used to split zones (side/top).

Drawings are x–y plane projections (top view). Used only for display and analysis (CoP, zones), never for recorded values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

GEOMETRY_DIR = Path(__file__).resolve().parent / "geometry"
# when referred to by a name other than the PXSR sensor label (model in config.yaml `sensor_types`, paxtest file names)
ALIASES = {"S2015E": "S2015"}


@dataclass(frozen=True)
class Geometry:
    label: str
    taxels: np.ndarray      # (N, 3) mm
    normals: np.ndarray     # (N, 3)
    surface: np.ndarray     # (M, 3) mm
    neighbor: np.ndarray    # (M, 4) taxel index
    shape: np.ndarray       # (M, 4) weights

    @property
    def n_taxels(self) -> int:
        return len(self.taxels)

    def surface_values(self, taxel_values: np.ndarray) -> np.ndarray:
        """Surface point value = sum of neighbor taxel value × shape weight (PXSR display coloring). taxel_values: (N,) or (n, N)."""
        v = np.asarray(taxel_values, dtype=float)
        return (v[..., self.neighbor] * self.shape).sum(axis=-1)

    def cop(self, taxel_z: np.ndarray, min_sum: float = 0.0) -> np.ndarray:
        """Center of pressure (mm): taxel positions averaged with taxel Z weights (negatives → 0) (CoP formula of paxtest `cop_and_torque`).
        taxel_z: (N,) or (n, N). NaN if the sum is ≤ min_sum."""
        w = np.clip(np.atleast_2d(np.asarray(taxel_z, dtype=float)), 0, None)
        s = w.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            c = (w @ self.taxels) / s[:, None]
        c[s <= min_sum] = np.nan
        return c if np.ndim(taxel_z) == 2 else c[0]


def model_label(name: str) -> str:
    return ALIASES.get(name, name)


def has_geometry(name: str) -> bool:
    return (GEOMETRY_DIR / f"{model_label(name)}.json").is_file()


def load_geometry(name: str) -> Optional[Geometry]:
    """Load the point model by sensor label (S1813E, S2015). None for an unknown model."""
    return _load(model_label(name))


@lru_cache(maxsize=None)
def _load(label: str) -> Optional[Geometry]:
    path = GEOMETRY_DIR / f"{label}.json"
    if not path.is_file():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    rot = np.asarray(d["rotation"], dtype=float)
    return Geometry(
        label=d["model"],
        taxels=np.asarray(d["taxels_mm"], dtype=float),
        normals=rot[:, 6:9],
        surface=np.asarray([p["position"] for p in d["surface"]], dtype=float),
        neighbor=np.asarray([p["neighbor"] for p in d["surface"]], dtype=int),
        shape=np.asarray([p["shape"] for p in d["surface"]], dtype=float),
    )
