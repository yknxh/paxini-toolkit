"""PXSR 번들에서 센서 점 모델을 꺼내 `paxkit/device/geometry/<label>.json`으로 저장한다 (계획 P6-3).

    python tools/extract_geometry.py [--bundle PATH]

PXSR 3D 화면이 쓰는 점 모델 `j4` 맵 (`j4.set("S1813E", ov0)`, `j4.set("S2015", sv0)`, ~4549551):
- `{position:[x,y,z], idx:i}` = taxel i의 위치 (mm).
- `{position:[x,y,z], neighbor:[a,b,c,d], shape:[wa,wb,wc,wd]}` = 표면 점. PXSR은 이웃 taxel 4개 값에
  shape 가중치를 곱해 더한 값으로 이 점을 칠한다.
- taxel 힘 표시용 회전 (`calculation`: S1813E `nv0`/`B6`, S2015 `rv0`/`U6`): taxel마다 3×3 (열 우선 9개).

값은 번들의 숫자 그대로 옮긴다 (JS 숫자 리터럴 → float).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "paxkit" / "device" / "geometry"
DEFAULT_BUNDLE = Path.home() / "AppData/Local/Programs/pxsr-gen3/resources/app/dist/index.3bcb906d.js"
MODELS = {"S1813E": ("ov0", "B6"), "S2015": ("sv0", "U6")}
NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?"


def _array_at(text: str, name: str):
    """`name=[ ... ]`의 대괄호 짝을 맞춰 배열 원문과 시작 offset을 돌려준다."""
    i = text.find(name + "=[")
    if i < 0 or not re.match(r"[,;\s(]", text[i - 1]):
        i = text.find("," + name + "=[") + 1
    if i <= 0:
        raise ValueError(f"{name} 없음")
    k0 = i + len(name) + 1
    depth = 0
    for k in range(k0, len(text)):
        c = text[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[k0:k + 1], i
    raise ValueError(f"{name} 끝 없음")


def _nums(s: str):
    return [float(x) for x in re.findall(NUM, s)]


def extract(text: str, label: str, points: str, rot: str) -> dict:
    src, off = _array_at(text, points)
    taxels, surface = {}, []
    for body in re.findall(r"\{([^{}]*)\}", src):
        fields = dict(re.findall(r"(\w+):(\[[^\]]*\]|" + NUM + ")", body))
        pos = _nums(fields["position"])
        if "idx" in fields:
            taxels[int(float(fields["idx"]))] = pos
        else:
            surface.append({"position": pos, "neighbor": [int(v) for v in _nums(fields["neighbor"])],
                            "shape": _nums(fields["shape"])})
    rsrc, roff = _array_at(text, rot)
    rotation = [_nums(r) for r in re.findall(r"\[([^\[\]]*)\]", rsrc)]
    n = len(taxels)
    assert sorted(taxels) == list(range(n)) and len(rotation) == n, (label, n, len(rotation))
    m = re.search(r'j4\.set\("' + label + r'",' + points + r"\)", text)
    return {
        "model": label,
        "source": f"pxsr-gen3 v1.0.7 dist/index.3bcb906d.js: j4.set(\"{label}\", {points}) "
                  f"({points} ~{off}, j4 ~{m.start() if m else '?'}), 회전 {rot} ~{roff} (열 우선 3x3)",
        "units": "mm",
        "taxels_mm": [taxels[i] for i in range(n)],
        "surface": surface,
        "rotation": rotation,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    a = ap.parse_args()
    text = a.bundle.read_text(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for label, (points, rot) in MODELS.items():
        doc = extract(text, label, points, rot)
        path = OUT_DIR / f"{label}.json"
        path.write_bytes(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        print(f"{label}: taxel {len(doc['taxels_mm'])}, 표면 점 {len(doc['surface'])} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
