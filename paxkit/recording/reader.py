"""Reading PXSR-format CSV — our logs (`data/logs/`) and PXSR logs (`DataLogging/`) are read by the same function.

Column names `{channel}-{slot}-1x1-X|Y|Z` (resultant), `{channel}-{slot}-NxN-X|Y|Z[i]` (taxel). Values are raw ints (× 0.1 = N).
`Timestamp` is only `HH:mm:ss.SSS`, so the date comes from the file name (`YYYY-MM-DD-HHMMSS.csv`), and
if the time goes back by more than 12 hours it is taken as crossing midnight and a day is added (same rule as `paxtest` `parse_timestamp_column`).
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

SensorKey = Tuple[int, int]   # (channel, slot)


@dataclass
class SensorColumns:
    force: Dict[str, str] = field(default_factory=dict)              # "X"/"Y"/"Z" → column name
    taxels: Dict[str, List[str]] = field(default_factory=dict)       # "X"/"Y"/"Z" → column names in taxel order

    @property
    def n_taxels(self) -> int:
        return len(self.taxels.get("Z", []))


def sensor_columns(header: List[str]) -> Dict[SensorKey, SensorColumns]:
    """Header → {(channel, slot): column names} (in header order)."""
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
    """Local midnight of the file name date (Unix s). If no date, the date of the modification time."""
    m = FILE_DATE_RX.search(path.name)
    if m:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        d = datetime.fromtimestamp(path.stat().st_mtime).replace(hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp()


def timestamps_to_unix(text: pd.Series, day0: float) -> np.ndarray:
    """`HH:mm:ss.SSS` column → Unix s. Handles crossing midnight."""
    parts = text.astype(str).str.split(":", expand=True)
    sec = (parts[0].astype(int) * 3600 + parts[1].astype(int) * 60).to_numpy(dtype=float) \
        + parts[2].astype(float).to_numpy()
    day = np.concatenate([[0], np.cumsum(np.diff(sec) < -43200)]) if sec.size else sec
    return day0 + sec + day * 86400.0


@dataclass
class LogData:
    path: Path
    header: List[str]
    t: np.ndarray                 # Unix s per row
    raw: pd.DataFrame             # raw values without Timestamp (column names = header)
    sensors: Dict[SensorKey, SensorColumns]

    def force_raw(self, key: SensorKey) -> np.ndarray:
        """(n, 3) resultant raw X, Y, Z."""
        c = self.sensors[key].force
        return self.raw[[c["X"], c["Y"], c["Z"]]].to_numpy(dtype=float)

    def taxel_raw(self, key: SensorKey, axis: str = "Z") -> np.ndarray:
        """(n, taxel count) taxel raw of one axis."""
        return self.raw[self.sensors[key].taxels.get(axis, [])].to_numpy(dtype=float)


def read_log(path: Union[str, Path]) -> LogData:
    path = Path(path)
    df = pd.read_csv(path, dtype={"Timestamp": str}, low_memory=False)
    header = list(map(str, df.columns))
    t = timestamps_to_unix(df["Timestamp"], file_day0(path)) if len(df) else np.zeros(0)
    return LogData(path=path, header=header, t=t, raw=df.drop(columns=["Timestamp"]),
                   sensors=sensor_columns(header))
