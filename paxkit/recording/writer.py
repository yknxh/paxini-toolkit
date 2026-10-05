"""데이터 로깅 — PXSR 기록 시작/정지 버튼과 기록 클래스(`gs0`, `cs0`)를 옮긴다.

| PXSR (offset)                         | 여기 |
|---------------------------------------|------|
| `K0` 기록 시작 (Hh0 안, ~454200)      | `CsvRecorder.start()` |
| `d2` 행 추가 (`y0`가 1008마다 호출)   | `CsvRecorder.on_frame()` |
| `G1` 기록 정지                        | `CsvRecorder.stop()` |
| `gs0` (343437)                        | 파일명은 시작 시각, 첫 행에서 헤더(`W0`) 확정 |
| `cs0` (342548)                        | 첫 행에서 writer 생성 + 200 ms 주기 flush, 정지 시 남은 행 flush |

파일 바이트 규칙은 `pxsr_csv.py`. 동작 순서:
- 시작: 파일명만 정한다. 파일은 아직 만들지 않는다.
- 첫 행: 헤더 확정, 200 ms 주기 flush 시작. 파일은 첫 flush 때 생긴다 (그 전에 정지하면 정지 때).
- 행이 하나도 없이 정지하면 파일이 생기지 않는다.
- 같은 초에 두 번 시작하면 파일명이 같아 나중 기록의 첫 쓰기(`'w'`)가 앞 파일을 덮어쓴다 (PXSR과 같음).

PXSR과 다른 점 (파일 바이트에는 영향 없음):
- PXSR은 flush를 기다리지 않고 다음 flush를 시작할 수 있어, 첫 쓰기가 끝나기 전에 두 번째 쓰기가 시작되면
  헤더가 두 번 들어갈 수 있다 (쓰기 1회는 수 ms라 실제로는 생기지 않음, PXSR CSV 39개에서 0건).
  여기서는 쓰기를 차례로 한다.
- 수신 스레드가 디스크 쓰기로 막히지 않도록 flush는 별도 스레드가 한다.

`.json` 사이드카는 PXSR에 없는 추가 파일이다 (사용자 결정, CLAUDE.md). CSV 바이트에는 영향이 없다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..paths import data_path
from . import pxsr_csv

log = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 0.2   # gs0: `new cs0(filename).writeHeader(o).setInterval(200)`
SIDECAR_FORMAT = "paxkit-log-sidecar/1"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


class CsvRecorder:
    """센서 하나(`UsbSensor`)의 데이터 로깅. 시작할 때마다 새 객체를 만든다 (PXSR도 `new gs0`).

    source는 `sensors`(PXSR `v.value`)와 `add_sink(fn, on_stop)` / `remove_sink(fn)`를 가진 리더.
    `on_frame`은 리더 스레드에서 불리고, 행은 그 순간의 `source.sensors` 전체로 만든다 (PXSR `y0`).
    """

    def __init__(self, source, directory: Optional[Path] = None, *, interval: float = FLUSH_INTERVAL_S,
                 sidecar: bool = True, info: Optional[Dict[str, Any]] = None) -> None:
        self.source = source
        self.directory = Path(directory) if directory is not None else None
        self.interval = interval
        self.sidecar = sidecar
        self.info: Dict[str, Any] = dict(info or {})   # 사이드카에 넣을 부가 정보 (연결 모드, 포트 등)
        self.memo = ""

        self.path: Optional[Path] = None
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.line_count = 0             # PXSR `e.value.lineCount`
        self.header: Optional[List[str]] = None
        self.events: List[Dict[str, Any]] = []
        self.error = ""

        self._active = False            # PXSR `o.value`
        self._lock = threading.Lock()   # 상태·행 캐시
        self._io_lock = threading.Lock()   # flush를 차례로
        self._cache: List[list] = []    # cs0 `dataCache`
        self._writer: Optional[pxsr_csv.CsvFileWriter] = None
        self._layout: List[Dict[str, Any]] = []
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._attached = False

    # ── PXSR 버튼 ────────────────────────────────────────────────────
    def start(self, now: Optional[datetime] = None, *, attach: bool = True) -> Path:
        """`K0`: `new gs0(...)` → 파일명 = 지금 시각. attach=False면 sink 등록 없이 `on_frame`을 직접 부른다."""
        start = now or datetime.now()
        directory = self.directory or data_path("logs")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / pxsr_csv.log_filename(start)
        self.started_at = start.timestamp()
        self._active = True
        if attach:
            self.source.add_sink(self.on_frame, on_stop=self.stop)
            self._attached = True
            if hasattr(self.source, "add_listener"):
                self.source.add_listener(self._on_source_event)
        return self.path

    def _on_source_event(self, kind: str, info: Dict[str, Any]) -> None:
        """리더의 수신 멈춤을 사이드카 events에 남긴다 (리더가 이벤트 뒤에 기록을 멈춘다)."""
        if kind == "stalled":
            last = info.get("t")
            self.note_event("stalled", None, last_frame=_iso(last) if last else None, stall_s=info.get("stall_s"),
                            message="USB 수신 멈춤 → 마지막 프레임까지 저장하고 기록 정지")

    def on_frame(self, frame) -> None:
        """`d2(T1)` → `gs0.writeData` → `cs0.writeData`."""
        with self._lock:
            if not self._active:
                return
            sensors = self.source.sensors
            row = pxsr_csv.build_row(pxsr_csv.format_timestamp(frame.t), sensors)
            if self._writer is None:
                # gs0: 첫 writeData에서 createScvHeaderCallback(=W0)으로 헤더 → cs0 생성
                # cs0: 첫 writeData에서 createArrayCsvWriter + startFlushLoop
                self.header = pxsr_csv.build_header(sensors)
                self._writer = pxsr_csv.CsvFileWriter(self.path, self.header)
                self._layout = self._sensor_layout(sensors)
                self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="CsvRecorder")
                self._thread.start()
            self._cache.append(row)
            self.line_count += 1

    def stop(self) -> None:
        """`G1`: `o.value=false` → `gs0.stop()` (남은 행 flush). 여러 번 불러도 된다."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.stopped_at = time.time()
        if self._attached:
            self.source.remove_sink(self.on_frame)
            if hasattr(self.source, "remove_listener"):
                self.source.remove_listener(self._on_source_event)
            self._attached = False
        self._stop_evt.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        self._flush()
        self._write_sidecar()

    # ── 상태 ─────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        return self._active

    @property
    def file_exists(self) -> bool:
        return self._writer is not None and self._writer.append

    def note_event(self, kind: str, t: Optional[float] = None, **info: Any) -> None:
        """캘리브레이션 등 기록 중 일어난 일을 사이드카에 남긴다 (CSV에는 넣지 않음)."""
        with self._lock:
            if self._active:
                self.events.append({"time": _iso(t if t is not None else time.time()), "kind": kind, **info})

    # ── 내부 ─────────────────────────────────────────────────────────
    def _flush_loop(self) -> None:
        """cs0 `startFlushLoop`: `setInterval(() => flushData(false), 200)`."""
        while not self._stop_evt.wait(self.interval):
            first = not self.file_exists
            self._flush()
            if first and self.file_exists:
                self._write_sidecar()

    def _flush(self) -> None:
        """cs0 `flushData`: 캐시가 비어 있으면 아무것도 하지 않는다."""
        with self._io_lock:
            with self._lock:
                rows, self._cache = self._cache, []
            if not rows or self._writer is None:
                return
            try:
                self._writer.write_records(rows)
            except OSError as e:
                log.exception("CSV 쓰기 실패")
                self.error = str(e)

    def _sensor_layout(self, sensors) -> List[Dict[str, Any]]:
        src = self.source
        out = []
        for ch, slots in enumerate(sensors):
            for slot, f in enumerate(slots or ()):
                if f is None:
                    continue
                d = {"channel": ch, "slot": slot, "sensor": f.sensor, "taxels": len(f.grid) // 3}
                infos = getattr(src, "infos", {}) or {}
                if infos.get(slot):
                    d["version"] = infos[slot]
                out.append(d)
        return out

    def _write_sidecar(self) -> None:
        if not self.sidecar or not self.file_exists:
            return
        with self._lock:
            doc = {
                "format": SIDECAR_FORMAT,
                "csv": self.path.name,
                "started": _iso(self.started_at),
                "stopped": _iso(self.stopped_at) if self.stopped_at else None,
                "rows": self.line_count,
                **self.info,
                "sensors": self._layout,
                "events": list(self.events),
                "memo": self.memo,
            }
        try:
            self.path.with_suffix(".json").write_bytes(
                json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        except OSError:
            log.exception("사이드카 쓰기 실패")
