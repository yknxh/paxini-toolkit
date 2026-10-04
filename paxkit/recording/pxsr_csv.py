"""PXSR 데이터 로깅 CSV의 바이트 규칙 — PXSR 코드와 `csv-writer` 1.6.0 소스를 1:1로 옮긴다.

| 출처                                                     | 여기 |
|----------------------------------------------------------|------|
| PXSR `rs0` 파일명 (338314)                               | `rs0()`, `log_filename()` |
| PXSR `U2().format("HH:mm:ss.SSS")` (dayjs, `y0` 448712)  | `format_timestamp()` |
| PXSR `W0` 헤더 (453877)                                  | `build_header()` |
| PXSR `y0` 1008 처리의 행 `T1` (449663)                   | `build_row()` |
| csv-writer `DefaultFieldStringifier`·`CsvStringifier`    | `stringify_field()`, `csv_line()`, `join_records()` |
| csv-writer `CsvWriter` + `FileWriter`                    | `CsvFileWriter` |

`sensors`는 PXSR 화면 상태 `v.value`와 같은 모양이다: 채널 목록 → 슬롯 목록 → 그 슬롯의 마지막 프레임
(빈 자리는 None = JS 배열의 빈 칸). `UsbSensor.sensors`가 이 값을 PXSR과 같은 시점에 갱신한다.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Union

Cell = Union[int, str, bool, None]

FIELD_DELIMITER = ","     # csv-writer DEFAULT_FIELD_DELIMITER
RECORD_DELIMITER = "\n"   # csv-writer DEFAULT_RECORD_DELIMITER (CRLF 아님)
FILENAME_FORMAT = "YYYY-MM-dd-HHmmSS"   # gs0 생성자


# ── 파일명 ───────────────────────────────────────────────────────────
def rs0(t: datetime, e: str) -> str:
    """PXSR `rs0(t, e)` (338314). 키 순서·첫 번째 일치만 바꾸는 동작까지 그대로."""
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
    """gs0: `rs0(new Date, "YYYY-MM-dd-HHmmSS") + ".csv"` — 기록 시작 시각(로컬)."""
    return rs0(start, FILENAME_FORMAT) + ".csv"


# ── Timestamp ────────────────────────────────────────────────────────
def format_timestamp(t: float) -> str:
    """dayjs `format("HH:mm:ss.SSS")`, 로컬 시간.

    JS Date는 정수 ms(내림)다. t(Unix 초, float)는 µs 단위로 먼저 반올림해 float 오차로
    ms가 하나 내려가는 것을 막는다 (float64는 현재 시각에서 약 0.24 µs 정밀도).
    """
    ms = round(t * 1_000_000) // 1000
    lt = time.localtime(ms // 1000)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.{ms % 1000:03d}"


# ── 헤더·행 (PXSR) ───────────────────────────────────────────────────
def build_header(sensors: Sequence[Optional[Sequence]]) -> List[str]:
    """PXSR `W0` (453877).

    `v.value.forEach((U0,Q0)=>{U0&&U0.forEach((l1,g1)=>{...})})` — 빈 칸(None)은 forEach가 건너뛴다.
    taxel 열 수는 `for(o2=0; o2<multiGrid.length/3; o2++)` 이므로 ceil(len/3).
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
    """PXSR `y0` 1008 처리 (449663): `T1=[시각]; v.value.forEach(f3=>f3.forEach(D1=>{D1&&T1.push(
    ...D1.combineForce.concat(D1.multiGrid))}))`."""
    t1: List[Cell] = [timestamp]
    for f3 in sensors:
        for d1 in f3 or ():   # USB는 채널 0만 있고 항상 배열이다
            if d1 is not None:
                t1.extend(d1.combine)
                t1.extend(d1.grid)
    return t1


# ── csv-writer 1.6.0 ─────────────────────────────────────────────────
def js_string(value: Cell) -> str:
    """JS `String(value)` 중 기록에 나오는 타입만 (정수·문자열·불리언).

    float는 JS 숫자 표기(지수 규칙 등)가 Python과 달라 받지 않는다 (PXSR raw 값은 모두 정수).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) >= 10 ** 21:   # JS는 1e21부터 지수 표기
            raise ValueError(f"JS 지수 표기 범위의 정수는 지원하지 않음: {value}")
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"지원하지 않는 값 타입: {type(value).__name__}")


def stringify_field(value: Cell) -> str:
    """`DefaultFieldStringifier.stringify`: 빈 값(undefined·null·'')은 빈 칸,
    `,` `\\n` `"`가 있으면 따옴표로 감싸고 `"`는 `""`로 (`\\r`만 있으면 감싸지 않음)."""
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
    """`CsvStringifier.joinRecords`: 마지막 행 뒤에도 구분자."""
    return RECORD_DELIMITER.join(lines) + RECORD_DELIMITER


class CsvFileWriter:
    """`createArrayCsvWriter({path, header})`가 만드는 `CsvWriter` + `FileWriter`.

    - 첫 쓰기는 flag `'w'`(새로 만들거나 덮어씀) + 헤더 행, 이후는 `'a'`(덧붙임).
    - 쓸 때마다 파일을 열고 닫는다 (`fs.writeFile`). 인코딩 UTF-8, BOM 없음.
    - header가 비어 있으면 헤더 행을 쓰지 않는다 (cs0: `...this.header.length&&{header}`).
    """

    def __init__(self, path: Union[str, Path], header: Optional[Sequence[Cell]] = None) -> None:
        self.path = Path(path)
        self.header = list(header) if header else None
        self.append = False   # CsvWriter.append / FileWriter.append (기본값 false)

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
