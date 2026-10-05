"""Force gauge (plan P5). Not a PXSR replacement target, so the paxtest reader was ported as-is, with only the clock aligned to the sensor."""
from .reader import GaugeBase, SerialGauge, SimGauge
from .sync import sensor_lag, xcorr_offset

__all__ = ["GaugeBase", "SerialGauge", "SimGauge", "sensor_lag", "xcorr_offset"]
