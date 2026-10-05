"""게이지 테스트 세션 폴더를 (재)분석한다 (계획 P6-2). GUI "재분석" 버튼과 같은 코드.

    python tools/bench_analyze.py <세션 폴더>... [--set 이름=값 ...]
    python tools/bench_analyze.py --latest

설정은 기본으로 세션 `meta.json`에 저장된 값을 쓴다. --set으로 바꾸면 쓴 값이 result.json에 남는다.
결과(samples.csv, metrics.csv, result.json, plots/, report.html)는 세션 폴더에 덮어쓴다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.bench import analyze_session  # noqa: E402
from paxkit.paths import data_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", type=Path)
    ap.add_argument("--latest", action="store_true", help="data/bench/의 가장 최근 세션")
    ap.add_argument("--set", action="append", default=[], metavar="이름=값")
    a = ap.parse_args()
    folders = list(a.folders)
    if a.latest:
        cands = sorted(p for p in data_path("bench").iterdir() if (p / "meta.json").is_file())
        folders += cands[-1:]
    if not folders:
        ap.error("세션 폴더를 주거나 --latest")
    override = {}
    for kv in a.set:
        k, _, v = kv.partition("=")
        override[k.strip()] = yaml.safe_load(v)
    for f in folders:
        res = analyze_session(f, override or None)
        m = res.metrics.iloc[0]
        print(f"{f.name}: 안정 샘플 {int(m['n'])}, bias {m['bias_N']}, RMSE {m['rmse_N']} N, "
              f"기울기 {m['slope']}, R² {m['r2']}")
        for w in res.warnings:
            print(f"  경고: {w}")
        print(f"  → {f / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
