"""센서 리더가 내보내는 프레임 (수신 프레임 1개 = PXSR CSV 1행의 센서 부분)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

RawValue = Optional[int]   # 프레임이 짧으면 PXSR처럼 `undefined`(None)가 들어갈 수 있다


@dataclass(frozen=True)
class Frame:
    t: float                       # 수신 처리 시각 (Unix 초, PXSR `dayjs()` 시점)
    channel: int                   # CSV 열 `{channel}-{slot}-...` (USB는 항상 0)
    slot: int                      # USB: 응답한 serviceID − 1
    sensor: str                    # PXSR 센서 타입 label (예: S1813E, S2015)
    combine: Tuple[RawValue, ...]  # combineForce (X, Y, Z) raw
    grid: Tuple[RawValue, ...]     # multiGrid: taxel마다 X, Y, Z raw를 이어 붙인 것

    @property
    def values(self) -> Tuple[RawValue, ...]:
        """PXSR CSV 행에서 이 센서가 차지하는 값 (`combineForce.concat(multiGrid)`)."""
        return self.combine + self.grid
