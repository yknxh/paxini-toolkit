"""Build sensor zones (plan P6-3): 1 tip + (middle, root) × (left side, top, right side) 3 = 7 zones.

    python tools/make_zones.py [--check-only]

Rules (the user's 2026-10-05 manual assignment, turned into a rule):
- Rows: split taxel y (mm) with the two `ROW_SPLIT_MM` values. Large y (rounded end) is "tip", small y is "root".
  Tip = the rounded head (S2015: above 12/13/38/40, S1813E: above 10/11/19/23). The row right below it
  (S2015 9/11/19/27/32/39/47, S1813E 4/9/16/22/29) is middle. The split values fall between taxel rows.
- Columns (middle and root only): taxel x (mm) within ±`SIDE_X_MM` is top, otherwise left/right side by the sign of x.
  The tip is not split into sides (user choice).
  Splitting by normal tilt was not used: root edge taxels (normals also tilt toward −y, so the x tilt is small)
  ended up in "top", which did not match the picture.
The result is saved as taxel lists in `paxkit/bench/zones/<label>.json` (may be edited by hand afterwards).
Also writes a check figure `data/zones/<label>.png` (top view, right = +x, up = +y). --check-only keeps the json
and only redraws the figure.
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
ROWS = (("root", "Root"), ("mid", "Middle"), ("tip", "Tip"))
COLS = (("left", "left side"), ("top", "top"), ("right", "right side"))


def make(label: str) -> dict:
    g = load_geometry(label)
    x = g.taxels[:, 0]
    col = np.where(np.abs(x) < SIDE_X_MM[label], 1, np.where(x < 0, 0, 2))
    lo, hi = ROW_SPLIT_MM[label]
    y = g.taxels[:, 1]
    row = np.where(y < lo, 0, np.where(y < hi, 1, 2))
    zones = []
    tip = [int(i) for i in np.flatnonzero(row == 2)]
    zones.append({"id": "tip", "name": "Tip", "row": 0, "col": 1, "taxels": tip})   # the tip is not split into sides
    for r in (1, 0):             # display order: tip → root, left → right (tip at the top of the figure)
        for c in range(3):
            idx = [int(i) for i in np.flatnonzero((row == r) & (col == c))]
            zones.append({"id": f"{ROWS[r][0]}-{COLS[c][0]}", "name": f"{ROWS[r][1]} {COLS[c][1]}",
                          "row": 2 - r, "col": c, "taxels": idx})
    return {
        "model": label,
        "rule": f"tools/make_zones.py: y >= {hi:g} mm tip / otherwise y < {lo:g} mm root, else middle; "
                f"|x| < {SIDE_X_MM[label]:g} mm top, else left/right side by sign of x",
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
