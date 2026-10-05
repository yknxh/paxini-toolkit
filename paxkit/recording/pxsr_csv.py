"""Byte rules of the PXSR data logging CSV — 1:1 port of PXSR code and the `csv-writer` 1.6.0 source.

| Source                                                   | Here |
|----------------------------------------------------------|------|
| PXSR `rs0` file name (338314)                            | `rs0()`, `log_filename()` |
| PXSR `U2().format("HH:mm:ss.SSS")` (dayjs, `y0` 448712)  | `format_timestamp()` |
| PXSR `W0` header (453877)                                | `build_header()` |
| PXSR row `T1` in `y0` 1008 handling (449663)             | `build_row()` |
| csv-writer `DefaultFieldStringifier`·`CsvStringifier`    | `stringify_field()`, `csv_line()`, `join_records()` |
| csv-writer `CsvWriter` + `FileWriter`                    | `CsvFileWriter` |

`sensors` has the same shape as the PXSR screen state `v.value`: list of channels → list of slots → last frame of that slot
(empty places are None = holes in a JS array). `UsbSensor.sensors` updates it at the same points as PXSR.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Union

Cell = Union[int, str, bool, None]

FIELD_DELIMITER = ","     # csv-writer DEFAULT_FIELD_DELIMITER
RECORD_DELIMITER = "\n"   # csv-writer DEFAULT_RECORD_DELIMITER (not CRLF)
FILENAME_FORMAT = "YYYY-MM-dd-HHmmSS"   # gs0 constructor


# ── File name ────────────────────────────────────────────────────────
def rs0(t: datetime, e: str) -> str:
    """PXSR `rs0(t, e)` (338314). Kept as is, down to key order and replacing only the first match."""
    o = {
        "Y+": str(t.year), "y+": str(t.year),
        "M+": str(t.month),
        "D+": str(t.day), "d+": str(t.day),
        "H+": str(t.hour), "h+": str(t.hour),
        "m+": str(t.minute),
        "S+": str(t.second), "s+": str(t.second),
    }
    for n, v in o.items():
        i = re.search("(" + n + ")", e)
        if i:
            g = i.group(1)
            e = e.replace(g, v if len(g) == 1 else v.rjust(len(g), "0"), 1)
    return e


def log_filename(start: datetime) -> str:
    """gs0: `rs0(new Date, "YYYY-MM-dd-HHmmSS") + ".csv"` — logging start time (local)."""
    return rs0(start, FILENAME_FORMAT) + ".csv"


# ── Timestamp ────────────────────────────────────────────────────────
def format_timestamp(t: float) -> str:
    """dayjs `format("HH:mm:ss.SSS")`, local time.

    JS Date is integer ms (floored). t (Unix s, float) is first rounded to µs so float error
    cannot drop the ms by one (float64 has about 0.24 µs precision at the current time).
    """
    ms = round(t * 1_000_000) // 1000
    lt = time.localtime(ms // 1000)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.{ms % 1000:03d}"


# ── Header and rows (PXSR) ───────────────────────────────────────────
def build_header(sensors: Sequence[Optional[Sequence]]) -> List[str]:
    """PXSR `W0` (453877).

    `v.value.forEach((U0,Q0)=>{U0&&U0.forEach((l1,g1)=>{...})})` — forEach skips holes (None).
    The taxel column count is ceil(len/3), since it is `for(o2=0; o2<multiGrid.length/3; o2++)`.
    """
    d0 = ["Timestamp"]
    for q0, u0 in enumerate(sensors):
        if not u0:
            continue
        for g1, l1 in enumerate(u0):
            if l1 is None:
                continue
            d0 += [f"{q0}-{g1}-1x1-X", f"{q0}-{g1}-1x1-Y", f"{q0}-{g1}-1x1-Z"]
            n = len(l1.grid)
            if n > 0:
                for o2 in range(-(-n // 3)):
                    d0 += [f"{q0}-{g1}-NxN-X[{o2}]", f"{q0}-{g1}-NxN-Y[{o2}]", f"{q0}-{g1}-NxN-Z[{o2}]"]
    return d0


def build_row(timestamp: str, sensors: Sequence[Optional[Sequence]]) -> List[Cell]:
    """PXSR `y0` 1008 handling (449663): `T1=[time]; v.value.forEach(f3=>f3.forEach(D1=>{D1&&T1.push(
    ...D1.combineForce.concat(D1.multiGrid))}))`."""
    t1: List[Cell] = [timestamp]
    for f3 in sensors:
        for d1 in f3 or ():   # USB has only channel 0, and it is always an array
            if d1 is not None:
                t1.extend(d1.combine)
                t1.extend(d1.grid)
    return t1


# ── csv-writer 1.6.0 ─────────────────────────────────────────────────
def js_string(value: Cell) -> str:
    """JS `String(value)`, only for the types that appear in logs (int, string, boolean).

    floats are rejected because JS number formatting (exponent rules etc.) differs from Python (PXSR raw values are all ints).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) >= 10 ** 21:   # JS uses exponent notation from 1e21
            raise ValueError(f"integers in JS exponent-notation range are not supported: {value}")
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"unsupported value type: {type(value).__name__}")


def stringify_field(value: Cell) -> str:
    """`DefaultFieldStringifier.stringify`: empty values (undefined, null, '') become an empty field;
    quoted if it contains `,` `\\n` or `"`, with `"` → `""` (`\\r` alone is not quoted)."""
    if value is None or (isinstance(value, str) and value == ""):
        return ""
    s = js_string(value)
    if FIELD_DELIMITER in s or "\n" in s or '"' in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def csv_line(record: Sequence[Cell]) -> str:
    """`CsvStringifier.getCsvLine`."""
    return FIELD_DELIMITER.join(stringify_field(v) for v in record)


def join_records(lines: Sequence[str]) -> str:
    """`CsvStringifier.joinRecords`: delimiter after the last row too."""
    return RECORD_DELIMITER.join(lines) + RECORD_DELIMITER


class CsvFileWriter:
    """`CsvWriter` + `FileWriter` as created by `createArrayCsvWriter({path, header})`.

    - First write uses flag `'w'` (create or overwrite) + header row, later ones `'a'` (append).
    - The file is opened and closed on every write (`fs.writeFile`). UTF-8, no BOM.
    - No header row if header is empty (cs0: `...this.header.length&&{header}`).
    """

    def __init__(self, path: Union[str, Path], header: Optional[Sequence[Cell]] = None) -> None:
        self.path = Path(path)
        self.header = list(header) if header else None
        self.append = False   # CsvWriter.append / FileWriter.append (default false)

    def header_string(self) -> str:
        if self.append or not self.header:
            return ""
        return join_records([csv_line(self.header)])

    def write_records(self, records: Sequence[Sequence[Cell]]) -> None:
        """`CsvWriter.writeRecords`."""
        write_string = self.header_string() + join_records([csv_line(r) for r in records])
        with open(self.path, "ab" if self.append else "wb") as f:
            f.write(write_string.encode("utf-8"))
        self.append = True
