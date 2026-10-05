"""Gauge test (bench, plan P6): continuous-press method. Not a PXSR reimplementation, so the byte-identity rule does not apply.

Sensor recording uses the same `CsvRecorder` as regular logging (PXSR format as-is), and no correction is applied to sensor values.
Produces numbers and plots only, with no pass/fail judgment.
"""
from .analyze import BenchResult, analyze_session, load_result
from .coverage import Coverage
from .session import BenchSession, noload_check
from .settings import bench_settings
from .zones import ZoneSet, load_zones

__all__ = ["BenchResult", "BenchSession", "Coverage", "ZoneSet", "analyze_session", "bench_settings",
           "load_result", "load_zones", "noload_check"]
