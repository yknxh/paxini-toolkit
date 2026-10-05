"""Values the app remembers (`data/state.json`).

PXSR saves the last used sensor type in `serialPort.specification` of `config/sensor.json`, and on the next
connection, if there is no version response, requests data with that type's taxel count (`K`, 456723).
That value is saved here for the same behavior. The default S1813E is the value in the PXSR install's `config/sensor.json`.
Otherwise only UI choices are saved (`live_heatmap`: live tab heatmap on, default off).
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
