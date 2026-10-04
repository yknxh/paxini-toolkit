"""앱이 기억하는 값 (`data/state.json`).

PXSR은 마지막으로 쓴 센서 타입을 `config/sensor.json`의 `serialPort.specification`에 저장하고,
다음 연결에서 버전 응답이 없을 때 그 타입의 taxel 수로 데이터를 요청한다 (`K`, 456723).
같은 동작을 위해 그 값만 저장한다. 기본값 S1813E는 PXSR 설치본 `config/sensor.json`의 값.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .paths import DATA_DIR

STATE_FILE = DATA_DIR / "state.json"
DEFAULTS: Dict[str, Any] = {"specification": "S1813E"}


def load_state() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    try:
        out.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return out


def save_state(**values: Any) -> None:
    st = load_state()
    st.update(values)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_bytes(json.dumps(st, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
