"""센서 점 모델 (PXSR 3D 화면의 `j4` 맵, `tools/extract_geometry.py`로 추출).

- `taxels`: taxel i의 위치 (mm, PXSR 센서 좌표). paxtest geometry JSON과 같은 값 (출처가 같음).
- `surface`: 표면 점 위치와 이웃 taxel 4개·가중치. PXSR은 이웃 taxel 값 × 가중치의 합으로 표면 점을 칠한다.
- `normals`: taxel 표면의 법선 (회전 행렬 `B6`/`U6`의 세 번째 열). 구역(옆면·윗면) 나누기에 쓴다.

그림은 x–y 평면 투영 (위에서 본 모습). 화면 표시·분석(CoP, 구역)에만 쓰고 기록 값에는 쓰지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

GEOMETRY_DIR = Path(__file__).resolve().parent / "geometry"
# PXSR 센서 label이 아닌 이름으로 불릴 때 (config.yaml `sensor_types`의 model, paxtest 파일명)
ALIASES = {"S2015E": "S2015"}


@dataclass(frozen=True)
class Geometry:
    label: str
    taxels: np.ndarray      # (N, 3) mm
    normals: np.ndarray     # (N, 3)
    surface: np.ndarray     # (M, 3) mm
    neighbor: np.ndarray    # (M, 4) taxel 번호
    shape: np.ndarray       # (M, 4) 가중치

    @property
    def n_taxels(self) -> int:
        return len(self.taxels)

    def surface_values(self, taxel_values: np.ndarray) -> np.ndarray:
        """표면 점 값 = 이웃 taxel 값 × shape 가중치의 합 (PXSR 화면 칠하기). taxel_values: (N,) 또는 (n, N)."""
        v = np.asarray(taxel_values, dtype=float)
        return (v[..., self.neighbor] * self.shape).sum(axis=-1)

    def cop(self, taxel_z: np.ndarray, min_sum: float = 0.0) -> np.ndarray:
        """압력 중심 (mm): taxel Z(음수는 0)로 가중한 taxel 위치 평균 (paxtest `cop_and_torque`의 CoP 식).
        taxel_z: (N,) 또는 (n, N). 합이 min_sum 이하면 NaN."""
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
    """센서 label(S1813E, S2015)로 점 모델을 읽는다. 없는 모델이면 None."""
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
