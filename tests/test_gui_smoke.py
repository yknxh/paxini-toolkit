import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def test_main_window_opens():
    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    w.show()
    app.processEvents()
    assert w.windowTitle().startswith("paxkit")
    w.close()


def test_sim_connect_and_disconnect():
    """시뮬레이션 센서로 연결 → 그래프 갱신 → 해제가 GUI에서 동작한다."""
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d = w.device
    d.port_combo.setCurrentIndex(d.port_combo.findData("시뮬레이션: S2015"))
    d.connect_sensor()
    sensor = d.sensor
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert sensor.sensor_type.label == "S2015" and sensor.frame_count > 10
    assert w.live.values.text() != "-"
    d.disconnect_sensor()
    sensor.join(3)
    app.processEvents()
    d._refresh_status()
    assert d.sensor is None and not sensor.is_alive()
    w.close()


def test_sim_recording(tmp_path):
    """시뮬레이션 센서로 기록 시작 → 정지, 그리고 기록 중 연결 해제 시 기록이 멈춘다."""
    import json
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.main_window import MainWindow

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d, r = w.device, w.recording
    r.directory = tmp_path
    d.port_combo.setCurrentIndex(d.port_combo.findData("시뮬레이션: S1813E"))
    d.connect_sensor()
    pump(1.5)
    assert r.start_btn.isEnabled()
    r.start_btn.click()
    r.memo.setText("테스트 메모")
    pump(0.6)
    r.start_btn.click()
    rec = r.recorder
    assert not rec.active and rec.line_count > 10
    lines = rec.path.read_bytes().split(b"\n")
    assert lines[0].startswith(b"Timestamp,0-2-1x1-X") and len(lines) == rec.line_count + 2
    side = json.loads(rec.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["memo"] == "테스트 메모" and side["simulated"] and side["rows"] == rec.line_count

    time.sleep(1.1)   # 파일명이 초 단위라 같은 초에 다시 시작하면 덮어쓴다 (PXSR과 같음)
    r.start_btn.click()
    pump(0.3)
    rec2 = r.recorder
    assert rec2.active
    sensor = d.sensor
    d.disconnect_sensor()
    pump(0.1)
    assert not rec2.active   # 해제 시작과 함께 기록 정지
    sensor.join(3)
    pump(0.3)
    assert rec2.path.exists() and rec2.path != rec.path
    assert not r.start_btn.isEnabled()
    w.close()
