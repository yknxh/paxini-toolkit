"""PXSR 형식 CSV 읽기 — 우리 기록(`data/logs/`)과 PXSR 기록(`DataLogging/`)을 같은 함수로 읽는다.

열 이름 `{채널}-{슬롯}-1x1-X|Y|Z` (합력), `{채널}-{슬롯}-NxN-X|Y|Z[i]` (taxel). 값은 raw 정수 (× 0.1 = N).
`Timestamp`는 `HH:mm:ss.SSS`뿐이라 날짜는 파일명(`YYYY-MM-DD-HHMMSS.csv`)에서 가져오고,
시각이 12시간 넘게 뒤로 가면 자정을 넘은 것으로 보고 하루를 더한다 (`paxtest` `parse_timestamp_column`과 같은 규칙).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Union

import numpy as np
import pandas as pd

FILE_DATE_RX = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
FORCE_COL_RX = re.compile(r"^(\d+)-(\d+)-1x1-([XYZ])$")
TAXEL_COL_RX = re.compile(r"^(\d+)-(\d+)-NxN-([XYZ])\[(\d+)\]$")

SensorKey = Tuple[int, int]   # (채널, 슬롯)


@dataclass
class SensorColumns:
    force: Dict[str, str] = field(default_factory=dict)              # "X"/"Y"/"Z" → 열 이름
    taxels: Dict[str, List[str]] = field(default_factory=dict)       # "X"/"Y"/"Z" → taxel 번호 순 열 이름

    @property
    def n_taxels(self) -> int:
        return len(self.taxels.get("Z", []))


def sensor_columns(header: List[str]) -> Dict[SensorKey, SensorColumns]:
    """헤더 → {(채널, 슬롯): 열 이름} (헤더에 나온 순서)."""
    out: Dict[SensorKey, SensorColumns] = {}
    found: Dict[SensorKey, Dict[str, Dict[int, str]]] = {}
    for h in header:
        m = FORCE_COL_RX.match(h)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            out.setdefault(key, SensorColumns()).force[m.group(3)] = h
            continue
        m = TAXEL_COL_RX.match(h)
        if m:
            key = (int(m.group(1)), int(m.group(2)))
            out.setdefault(key, SensorColumns())
            found.setdefault(key, {}).setdefault(m.group(3), {})[int(m.group(4))] = h
    for key, axes in found.items():
        out[key].taxels = {ax: [d[i] for i in sorted(d)] for ax, d in axes.items()}
    return out


def file_day0(path: Path) -> float:
    """파일명 날짜의 로컬 자정 (Unix 초). 날짜가 없으면 수정 시각의 날짜."""
    m = FILE_DATE_RX.search(path.name)
    if m:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        d = datetime.fromtimestamp(path.stat().st_mtime).replace(hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp()


def timestamps_to_unix(text: pd.Series, day0: float) -> np.ndarray:
    """`HH:mm:ss.SSS` 열 → Unix 초. 자정 넘김 처리."""
    parts = text.astype(str).str.split(":", expand=True)
    sec = (parts[0].astype(int) * 3600 + parts[1].astype(int) * 60).to_numpy(dtype=float) \
        + parts[2].astype(float).to_numpy()
    day = np.concatenate([[0], np.cumsum(np.diff(sec) < -43200)]) if sec.size else sec
    return day0 + sec + day * 86400.0


@dataclass
class LogData:
    path: Path
    header: List[str]
    t: np.ndarray                 # 행마다 Unix 초
    raw: pd.DataFrame             # Timestamp 를 뺀 raw 값 (열 이름 = 헤더)
    sensors: Dict[SensorKey, SensorColumns]

    def force_raw(self, key: SensorKey) -> np.ndarray:
        """(n, 3) 합력 raw X, Y, Z."""
        c = self.sensors[key].force
        return self.raw[[c["X"], c["Y"], c["Z"]]].to_numpy(dtype=float)

    def taxel_raw(self, key: SensorKey, axis: str = "Z") -> np.ndarray:
        """(n, taxel 수) 한 축의 taxel raw."""
        return self.raw[self.sensors[key].taxels.get(axis, [])].to_numpy(dtype=float)


def read_log(path: Union[str, Path]) -> LogData:
    path = Path(path)
    df = pd.read_csv(path, dtype={"Timestamp": str}, low_memory=False)
    header = list(map(str, df.columns))
    t = timestamps_to_unix(df["Timestamp"], file_day0(path)) if len(df) else np.zeros(0)
    return LogData(path=path, header=header, t=t, raw=df.drop(columns=["Timestamp"]),
                   sensors=sensor_columns(header))
