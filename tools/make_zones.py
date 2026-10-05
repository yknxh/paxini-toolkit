"""센서 구역 초안 만들기 (계획 P6-3): 세로 3 (뿌리·가운데·끝) × 가로 3 (왼쪽 옆면·윗면·오른쪽 옆면) = 9구역.

    python tools/make_zones.py [--check-only]

규칙 (초안, 사용자 확인 전):
- 가로: taxel x(mm)가 ±`SIDE_X_MM` 안이면 윗면, 밖이면 x 부호로 왼쪽/오른쪽 옆면 (위에서 본 그림의 세로 띠).
  법선 기울기로 나누면 뿌리 쪽 가장자리 taxel(법선이 −y로도 기울어 x 기울기가 작음)이 윗면에 들어가 그림과 어긋나서 쓰지 않았다.
- 세로: taxel y(mm)를 `ROW_SPLIT_MM` 두 값으로 나눈다. y가 큰 쪽(둥근 끝)이 "끝", 작은 쪽이 "뿌리".
  경계값은 taxel 줄 사이에 오도록 모델마다 정했다 (한 줄이 두 구역에 갈리지 않게).
결과는 `paxkit/bench/zones/<label>.json`에 taxel 목록으로 저장한다 (이후 손으로 고쳐도 된다).
확인용 그림 `data/zones/<label>.png` (위에서 본 모습, 오른쪽 = +x, 위 = +y)도 만든다. --check-only면 json은 두고 그림만.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.bench.zones import ZONES_DIR, load_zones  # noqa: E402
from paxkit.bench.plots import zone_check_figure  # noqa: E402
from paxkit.device.geometry import load_geometry  # noqa: E402
from paxkit.paths import data_path  # noqa: E402

SIDE_X_MM = {"S1813E": 3.0, "S2015": 4.0}
ROW_SPLIT_MM = {"S1813E": (4.0, 11.0), "S2015": (6.5, 13.0)}
ROWS = (("root", "뿌리"), ("mid", "가운데"), ("tip", "끝"))
COLS = (("left", "왼쪽 옆면"), ("top", "윗면"), ("right", "오른쪽 옆면"))


def make(label: str) -> dict:
    g = load_geometry(label)
    x = g.taxels[:, 0]
    col = np.where(np.abs(x) < SIDE_X_MM[label], 1, np.where(x < 0, 0, 2))
    lo, hi = ROW_SPLIT_MM[label]
    y = g.taxels[:, 1]
    row = np.where(y < lo, 0, np.where(y < hi, 1, 2))
    zones = []
    for r in (2, 1, 0):          # 표시 순서: 끝 → 뿌리, 왼쪽 → 오른쪽 (그림 위쪽이 끝)
        for c in range(3):
            idx = [int(i) for i in np.flatnonzero((row == r) & (col == c))]
            zones.append({"id": f"{ROWS[r][0]}-{COLS[c][0]}", "name": f"{ROWS[r][1]} {COLS[c][1]}",
                          "row": 2 - r, "col": c, "taxels": idx})
    return {
        "model": label,
        "rule": f"초안 (tools/make_zones.py): |x| < {SIDE_X_MM[label]:g} mm 윗면, 아니면 x 부호로 옆면 / "
                f"y < {lo:g} mm 뿌리, < {hi:g} mm 가운데, 나머지 끝",
        "zones": zones,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    ZONES_DIR.mkdir(parents=True, exist_ok=True)
    for label in ROW_SPLIT_MM:
        path = ZONES_DIR / f"{label}.json"
        if not a.check_only:
            doc = make(label)
            path.write_bytes(json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8") + b"\n")
        z = load_zones(label)
        out = data_path("zones") / f"{label}.png"
        zone_check_figure(z).savefig(out, dpi=130)
        print(f"{label}: " + ", ".join(f"{zz.name} {len(zz.taxels)}" for zz in z.zones) + f" -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
