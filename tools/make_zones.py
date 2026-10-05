"""센서 구역 만들기 (계획 P6-3): 끝 1 + 가운데·뿌리 × (왼쪽 옆면·윗면·오른쪽 옆면) 3 = 7구역.

    python tools/make_zones.py [--check-only]

규칙 (2026-10-05 사용자 지정 배정을 규칙으로 옮김):
- 세로: taxel y(mm)를 `ROW_SPLIT_MM` 두 값으로 나눈다. y가 큰 쪽(둥근 끝)이 "끝", 작은 쪽이 "뿌리".
  끝 = 둥근 머리 부분 (S2015: 12/13/38/40 위쪽, S1813E: 10/11/19/23 위쪽). 그 바로 아래 줄
  (S2015 9/11/19/27/32/39/47, S1813E 4/9/16/22/29)은 가운데. 경계값은 taxel 줄 사이에 온다.
- 가로 (가운데·뿌리만): taxel x(mm)가 ±`SIDE_X_MM` 안이면 윗면, 밖이면 x 부호로 왼쪽/오른쪽 옆면.
  끝은 옆면을 나누지 않는다 (사용자 지정).
  법선 기울기로 나누면 뿌리 쪽 가장자리 taxel(법선이 −y로도 기울어 x 기울기가 작음)이 윗면에 들어가 그림과 어긋나서 쓰지 않았다.
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
ROW_SPLIT_MM = {"S1813E": (4.0, 14.0), "S2015": (6.5, 16.0)}
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
    tip = [int(i) for i in np.flatnonzero(row == 2)]
    zones.append({"id": "tip", "name": "끝", "row": 0, "col": 1, "taxels": tip})   # 끝은 옆면을 나누지 않음
    for r in (1, 0):             # 표시 순서: 끝 → 뿌리, 왼쪽 → 오른쪽 (그림 위쪽이 끝)
        for c in range(3):
            idx = [int(i) for i in np.flatnonzero((row == r) & (col == c))]
            zones.append({"id": f"{ROWS[r][0]}-{COLS[c][0]}", "name": f"{ROWS[r][1]} {COLS[c][1]}",
                          "row": 2 - r, "col": c, "taxels": idx})
    return {
        "model": label,
        "rule": f"tools/make_zones.py: y ≥ {hi:g} mm 끝 / 나머지는 y < {lo:g} mm 뿌리, 아니면 가운데, "
                f"|x| < {SIDE_X_MM[label]:g} mm 윗면, 아니면 x 부호로 옆면",
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
