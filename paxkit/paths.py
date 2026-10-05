"""Repo root and data/ paths (based on the package location, so independent of the working directory (cwd)).

Data is stored in data/ inside the repo (not in AppData like PXSR). Assumes an editable install
(`pip install -e .`) that uses the repo in place.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


def data_path(*parts: str, create: bool = True) -> Path:
    """Folder path under data/. e.g. data_path("logs") → <repo>/data/logs (created if missing)."""
    p = DATA_DIR.joinpath(*parts)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p
