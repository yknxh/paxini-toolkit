"""config.yaml 로딩 (paxtest/config.py에서 이전)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .paths import DEFAULT_CONFIG_PATH


class Config:
    def __init__(self, data: Dict[str, Any], path: Path):
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        path = Path(path or DEFAULT_CONFIG_PATH).resolve()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data, path)

    def section(self, name: str) -> Dict[str, Any]:
        return self.data.get(name) or {}

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for key in dotted.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    def resolve_path(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (self.path.parent / p).resolve()

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.data)
