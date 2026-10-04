"""레포 루트와 data/ 경로 (패키지 위치 기준이라 실행 위치(cwd)와 무관).

데이터는 레포 안 data/ 에 저장한다 (PXSR처럼 AppData에 두지 않음). 레포를 그대로 쓰는
editable 설치(`pip install -e .`)를 전제로 한다.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


def data_path(*parts: str, create: bool = True) -> Path:
    """data/ 아래 폴더 경로. 예) data_path("logs") → <repo>/data/logs (없으면 만든다)."""
    p = DATA_DIR.joinpath(*parts)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p
