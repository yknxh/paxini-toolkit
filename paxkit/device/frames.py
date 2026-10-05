"""Frames emitted by the sensor reader (one received frame = the sensor part of one PXSR CSV row)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

RawValue = Optional[int]   # a short frame can hold `undefined` (None), as in PXSR


@dataclass(frozen=True)
class Frame:
    t: float                       # receive-handling time (Unix s, at PXSR `dayjs()`)
    channel: int                   # CSV column `{channel}-{slot}-...` (always 0 for USB)
    slot: int                      # USB: responding serviceID − 1
    sensor: str                    # PXSR sensor type label (e.g. S1813E, S2015)
    combine: Tuple[RawValue, ...]  # combineForce (X, Y, Z) raw
    grid: Tuple[RawValue, ...]     # multiGrid: X, Y, Z raw per taxel, concatenated

    @property
    def values(self) -> Tuple[RawValue, ...]:
        """This sensor's values in a PXSR CSV row (`combineForce.concat(multiGrid)`)."""
        return self.combine + self.grid
