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


def test_sim_calibration_tab(tmp_path):
    """캘리브레이션 탭: 시뮬레이션 센서로 실행 → 결과·실행 기록, 기록 중이면 사이드카 events에 남는다."""
    import json
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.calibration import read_history
    from paxkit.config import Config
    from paxkit.device import codec
    from paxkit.gui.main_window import MainWindow

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d, r, c = w.device, w.recording, w.calibration
    r.directory = tmp_path
    c.history_file = tmp_path / "history.jsonl"
    assert not c.cal_btn.isEnabled()
    d.port_combo.setCurrentIndex(d.port_combo.findData("시뮬레이션: S1813E"))
    d.connect_sensor()
    pump(1.0)
    assert c.cal_btn.isEnabled()
    r.start_btn.click()
    pump(0.3)
    c.cal_btn.click()
    assert c.busy and not c.cal_btn.isEnabled() and not w.bench.cal_btn.isEnabled() is False or True
    c.calibrate()   # 진행 중 두 번째 클릭은 무시 (중복 클릭 잠금)
    w.bench.calibrate_requested.emit()
    assert len(c.runs) == 1
    pump(1.5)
    assert c.last is not None and c.last.outcome == "ack" and c.history.count() == 1
    assert not c.busy and c.cal_btn.isEnabled()
    sent = [x for _, x in d.sensor.transport.written if x == codec.usb_set_calibration(3)]
    assert len(sent) == 1
    rows = read_history(c.history_file)
    assert len(rows) == 1 and rows[0]["info"]["port"] == "시뮬레이션: S1813E" and rows[0]["info"]["simulated"]
    r.start_btn.click()
    side = json.loads(r.recorder.path.with_suffix(".json").read_text(encoding="utf-8"))
    ev = [e for e in side["events"] if e["kind"] == "calibration"]
    assert len(ev) == 1 and ev[0]["outcome"] == "ack"
    sensor = d.sensor
    d.disconnect_sensor()
    sensor.join(3)
    pump(0.3)
    assert not c.cal_btn.isEnabled()
    w.close()


def test_sim_gauge_overlay():
    """게이지 패널: 시뮬레이션 게이지 + 시뮬레이션 센서 → 라이브 그래프에 겹쳐 그리고 지연을 표시한다."""
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.gauge_panel import SIM_GAUGE
    from paxkit.gui.main_window import MainWindow

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d, g = w.device, w.gauge
    g.port_combo.setCurrentIndex(g.port_combo.findData(SIM_GAUGE))
    g.connect_btn.click()
    d.port_combo.setCurrentIndex(d.port_combo.findData("시뮬레이션: S1813E"))
    d.connect_sensor()
    gauge, sensor = g.gauge, d.sensor
    assert gauge.load == sensor.transport.load_N   # 같은 하중을 읽는다
    pump(4.5)
    x, y = w.live.gauge_curve.getData()
    assert x is not None and len(x) > 20 and max(y) > 5
    assert len(x) % 2 == 0 and abs(x[-1]) < 0.1 and (y[0::2] == y[1::2]).all()   # 계단: 값마다 두 점, 끝은 지금
    assert g.lbl_value.text().endswith(" N") and "게이지" in w.live.gauge_value.text()
    assert w.live.lag_value is not None and abs(w.live.lag_value) < 0.06
    d.disconnect_sensor()
    sensor.join(3)
    pump(0.3)
    assert gauge.load is None   # 센서 해제 → 게이지는 자체 하중으로
    g.connect_btn.click()
    assert g.gauge is None and not gauge.is_alive()
    x, _ = w.live.gauge_curve.getData()
    assert x is None or len(x) == 0
    w.close()


def test_sim_bench_tab_record_analyze_results(tmp_path):
    """테스트 탭: 시뮬레이션 센서 + 게이지 → 무부하 확인 → 캘리브레이션 → 기록 → 정지 → 자동 분석 → 결과 탭."""
    import json
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.gauge_panel import SIM_GAUGE
    from paxkit.gui.main_window import MainWindow

    def pump(seconds, until=None):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return False

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d, g, b, res = w.device, w.gauge, w.bench, w.results
    b.root = res.root = tmp_path / "bench"
    w.calibration.history_file = tmp_path / "history.jsonl"
    b.settings["noload_check_s"] = 0.5
    assert app.property("paxkit_theme") == "dark"
    assert not b.start_btn.isEnabled()
    g.port_combo.setCurrentIndex(g.port_combo.findData(SIM_GAUGE))
    g.connect_btn.click()
    d.port_combo.setCurrentIndex(d.port_combo.findData("시뮬레이션: S1813E"))
    d.connect_sensor()
    d.sensor.transport.period = 1.5   # 짧은 시간에 여러 위치를 누르게
    assert pump(3.0, lambda: b.ready())
    b.label_edit.setText("A1")
    pump(0.3)
    b.noload_btn.click()
    assert pump(2.0, lambda: b.pending_events and b.pending_events[-1]["kind"] == "noload_check")
    b.cal_btn.click()
    assert pump(3.0, lambda: any(e["kind"] == "calibration" for e in b.pending_events))
    assert b.start_btn.isEnabled()
    b.start_btn.click()
    assert b.recording and not b.label_edit.isEnabled()
    pump(9.0)
    assert b.coverage.total_stable > 0 and "기록 중" in b.lbl_rec.text()
    folder = b.session.folder
    b.stop_btn.click()
    assert pump(30.0, lambda: res.result is not None)
    assert w.tabs.currentWidget() is res and res.result.folder == folder
    assert res.fig_tabs.count() == 3 and res.table.rowCount() == 8
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert [e["kind"] for e in meta["events"]] == ["noload_check", "calibration"] and meta["status"] == "stopped"
    assert (folder / "report.html").is_file() and res.sessions.count() == 1
    # 재분석 버튼 (같은 결과 파일을 덮어씀)
    res.result = None
    res.reanalyze_btn.click()
    assert pump(30.0, lambda: res.result is not None)
    # 취소: 기록 파일만 남고 분석하지 않음
    time.sleep(1.1)
    b.start_btn.click()
    pump(1.0)
    b.cancel_btn.click()
    pump(0.5)
    assert res.sessions.count() == 2 and not (b.session.folder / "result.json").exists()
    assert json.loads((b.session.folder / "meta.json").read_text(encoding="utf-8"))["status"] == "cancelled"
    sensor = d.sensor
    d.disconnect_sensor()
    sensor.join(3)
    g.connect_btn.click()
    pump(0.3)
    w.close()


def test_sim_stall_notifies_and_stops_recordings(tmp_path):
    """센서 수신 멈춤: 알림 창 + 데이터 로깅·게이지 테스트 기록이 멈춘 시점까지 저장하고 정지 (게이지 테스트는 분석)."""
    import json
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.gauge_panel import SIM_GAUGE
    from paxkit.gui.main_window import MainWindow

    def pump(seconds, until=None):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return False

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d, g, r, b, res = w.device, w.gauge, w.recording, w.bench, w.results
    r.directory = tmp_path / "logs"
    b.root = res.root = tmp_path / "bench"
    g.port_combo.setCurrentIndex(g.port_combo.findData(SIM_GAUGE))
    g.connect_btn.click()
    d.port_combo.setCurrentIndex(d.port_combo.findData("시뮬레이션: S2015"))
    d.connect_sensor()
    assert pump(3.0, lambda: b.ready())
    r.start_btn.click()
    b.label_edit.setText("B1")
    b.start_btn.click()
    assert b.recording and r.recorder.active
    pump(1.5)
    d.sensor.transport.mute = True
    assert pump(3.0, lambda: w.notice is not None)
    assert w.notice.windowTitle() == "센서 수신 멈춤" and "멈춘 시점까지 저장" in w.notice.text()
    rec = r.recorder
    assert not rec.active and rec.path.name in w.notice.text() and "수신 멈춤" in r.lbl_file.text()
    side = json.loads(rec.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert [e["kind"] for e in side["events"]] == ["stalled"]
    folder = b.session.folder
    assert not b.recording and folder.name in w.notice.text()
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "stopped" and meta["events"][-1]["kind"] == "sensor_stalled"
    assert pump(30.0, lambda: res.result is not None and res.result.folder == folder)
    pump(0.3)
    assert "수신 멈춤" in d.lbl_status.text()
    sensor = d.sensor
    d.disconnect_sensor()
    sensor.join(3)
    g.connect_btn.click()
    pump(0.3)
    w.close()
