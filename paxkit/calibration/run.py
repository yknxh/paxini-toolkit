"""캘리브레이션 1회 실행·결과 추적.

센서로 보내는 명령·순서·대기 시간은 리더(`UsbSensor.calibrate`, PXSR `O3` 454624)가 처리한다.
리더의 이벤트 루프 안에서 돌아야 PXSR과 실행 순서가 같기 때문이다.
여기서는 버튼을 누른 뒤 무슨 일이 있었는지만 기록한다 (센서로 보내는 명령은 없음, 앱 쪽 보정도 없음).

결과 판정은 PXSR 화면에 보이는 것과 같은 규칙:
- 응답(addr 3)이 오면 PXSR은 상태를 보지 않고 폴링을 재개하고, 메시지를 띄우지 않는다 → "ack".
- 응답의 기능 코드가 126이고 상태 바이트가 0이 아니면 PXSR은 `Setting.failed` 경고를 띄운다 → "failed"
  (그래도 폴링은 재개한다).
- 응답이 오지 않으면 PXSR은 폴링을 멈춘 채로 둔다 (재시도 없음) → "no_ack". 다시 연결해야 수신이 재개된다.
  응답 대기 시간(`ack_timeout`)은 PXSR에 없는 값이다 (화면 표시용).

전후 값(`before`, `after`)은 합력 raw 평균을 보여 주기만 한다 (측정 데이터에 적용하지 않음).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..paths import data_path

ACK_TIMEOUT_S = 2.0   # setCalibration 전송 후 이 시간까지 응답이 없으면 "no_ack"로 표시
WINDOW_S = 0.5        # 전후 값 평균 구간

OUTCOME_TEXT = {
    "pending": "진행 중",
    "ack": "응답 받음 (PXSR: 메시지 없음, 폴링 재개)",
    "failed": "실패 응답 (PXSR: Setting.failed 경고, 폴링 재개)",
    "no_ack": "응답 없음 (PXSR처럼 수신이 멈춘 상태, 다시 연결 필요)",
    "disconnected": "응답 전에 연결이 끊김",
}


def _iso(t: Optional[float]) -> Optional[str]:
    return None if t is None else datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


@dataclass
class CalibrationResult:
    requested: float                     # 버튼을 누른 PC 시각 (Unix 초)
    sent: Optional[float] = None         # setCalibration 전송 시각
    acked: Optional[float] = None        # 응답 처리 시각
    status: Optional[int] = None         # parseUsbData status
    function_code: Optional[int] = None  # 응답 기능 코드 (캡처는 121)
    failed: bool = False                 # PXSR이 Setting.failed 경고를 띄우는 응답
    outcome: str = "pending"             # pending | ack | failed | no_ack | disconnected
    before: Optional[List[float]] = None  # 요청 전 WINDOW_S 동안 합력 raw 평균 [X, Y, Z]
    after: Optional[List[float]] = None   # 응답 후 WINDOW_S 동안 합력 raw 평균
    info: Dict[str, Any] = field(default_factory=dict)   # 포트·센서 타입·serviceID·버전

    @property
    def done(self) -> bool:
        return self.outcome != "pending"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("requested", "sent", "acked"):
            d[k] = _iso(d[k])
        return d


def _mean(buffer, t0: float, t1: float) -> Optional[List[float]]:
    t, v = buffer.snapshot(since=t0)
    v = v[t <= t1]
    if len(v) == 0:
        return None
    return [round(float(x), 2) for x in np.mean(v, axis=0)]


class CalibrationRun:
    """버튼 한 번. `start()` 뒤 `poll()`을 주기적으로 부르거나 `wait()`로 끝날 때까지 기다린다.

    PXSR처럼 중복 클릭을 막지 않으므로, 진행 중에 또 누르면 각 실행은 자기 시작 뒤의 첫 전송·응답을 잡는다.
    """

    def __init__(self, sensor, info: Optional[Dict[str, Any]] = None, *,
                 ack_timeout: float = ACK_TIMEOUT_S, window: float = WINDOW_S) -> None:
        self.sensor = sensor
        self.ack_timeout = ack_timeout
        self.window = window
        self._lock = threading.Lock()
        self.result = CalibrationResult(requested=0.0, info={
            "sensor": sensor.sensor_type.label, "service_id": sensor.service_id,
            "slot": sensor.slot, "version": sensor.version, **(info or {})})

    def start(self) -> "CalibrationRun":
        r = self.result
        r.requested = self.sensor.clock.wall()
        r.before = _mean(self.sensor.buffer, r.requested - self.window, r.requested)
        self.sensor.add_listener(self._on_event)
        self.sensor.calibrate()
        return self

    def _on_event(self, kind: str, info: dict) -> None:
        with self._lock:
            r = self.result
            if r.done:
                return
            if kind == "calibration_sent" and r.sent is None:
                r.sent = info.get("t")
            elif kind == "calibration_ack" and r.sent is not None and r.acked is None:
                r.acked = info.get("t")
                r.status = info.get("status")
                r.function_code = info.get("function_code")
                r.failed = bool(info.get("failed"))
            elif kind in ("disconnected", "error") and r.acked is None:
                self._finish("disconnected")

    def _finish(self, outcome: str) -> None:
        self.result.outcome = outcome
        self.sensor.remove_listener(self._on_event)

    def poll(self, now: Optional[float] = None) -> bool:
        """끝났으면 True. now는 PC 시각 (기본 센서 시계)."""
        now = self.sensor.clock.wall() if now is None else now
        with self._lock:
            r = self.result
            if r.done:
                return True
            if r.acked is not None:
                if now >= r.acked + self.window:
                    r.after = _mean(self.sensor.buffer, r.acked, r.acked + self.window)
                    self._finish("failed" if r.failed else "ack")
            elif r.sent is not None and now >= r.sent + self.ack_timeout:
                self._finish("no_ack")
            return r.done

    def wait(self, timeout: float = 10.0, interval: float = 0.02) -> CalibrationResult:
        end = time.monotonic() + timeout
        while not self.poll() and time.monotonic() < end:
            time.sleep(interval)
        return self.result


def history_path() -> Path:
    return data_path("calibration") / "history.jsonl"


def append_history(result: CalibrationResult, path: Optional[Path] = None) -> Path:
    """캘리브레이션 실행 기록을 한 줄씩 덧붙인다 (`data/calibration/history.jsonl`). 값은 저장하지 않는다."""
    path = path or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write((json.dumps(result.to_dict(), ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def read_history(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path or history_path()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
