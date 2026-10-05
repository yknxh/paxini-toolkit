"""게이지 테스트 (bench, 계획 P6): 연속 누름 방식. PXSR 재구현이 아니므로 바이트 동일성 원칙과 무관하다.

센서 기록은 일반 로깅과 같은 `CsvRecorder`(PXSR 형식 그대로)이고, 센서 값에는 어떤 보정도 하지 않는다.
합격/불합격 판정 없이 수치·그래프만 낸다.
"""
from .analyze import BenchResult, analyze_session, load_result
from .coverage import Coverage
from .session import BenchSession, noload_check
from .settings import bench_settings
from .zones import ZoneSet, load_zones

__all__ = ["BenchResult", "BenchSession", "Coverage", "ZoneSet", "analyze_session", "bench_settings",
           "load_result", "load_zones", "noload_check"]
