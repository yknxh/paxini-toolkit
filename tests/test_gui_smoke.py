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
    """Simulated sensor: connect → plot updates → disconnect works in the GUI."""
    import time

    from PySide6.QtWidgets import QApplication

    from paxkit.config import Config
    from paxkit.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config.load())
    d = w.device
    d.port_combo.setCurrentIndex(d.port_combo.findData("Simulated: S2015"))
    d.connect_sensor()
    sensor = d.sensor
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert sensor.sensor_type.label == "S2015" and sensor.frame_count > 10
    assert w.live.values.text() != "-"
    texts = [d.events.item(i).text() for i in range(d.events.count())]
    assert any("Port opened" in t for t in texts) and any("Version reply" in t for t in texts)
    # heatmap (optional): level computation + painting
    m = w.live.map
    assert m.set_model("S2015")
    z = [0] * 52
    z[10] = 30
    m.show_values(z)
    assert m.levels[10] == 63 and m.levels.sum() == 63
    m.resize(200, 260)
    assert not m.grab().isNull()
    d.disconnect_sensor()
    sensor.join(3)
    app.processEvents()
    d._refresh_status()
    assert d.sensor is None and not sensor.is_alive()
    w.close()


def test_sim_recording(tmp_path):
    """Simulated sensor: start → stop recording, and recording stops when disconnecting mid-recording."""
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
    d.port_combo.setCurrentIndex(d.port_combo.findData("Simulated: S1813E"))
    d.connect_sensor()
    pump(1.5)
    assert r.start_btn.isEnabled()
    r.start_btn.click()
    r.memo.setText("test memo")
    pump(0.6)
    r.start_btn.click()
    rec = r.recorder
    assert not rec.active and rec.line_count > 10
    lines = rec.path.read_bytes().split(b"\n")
    assert lines[0].startswith(b"Timestamp,0-2-1x1-X") and len(lines) == rec.line_count + 2
    side = json.loads(rec.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["memo"] == "test memo" and side["simulated"] and side["rows"] == rec.line_count

    time.sleep(1.1)   # file names have 1 s resolution, so restarting within the same second overwrites (same as PXSR)
    r.start_btn.click()
    pump(0.3)
    rec2 = r.recorder
    assert rec2.active
    sensor = d.sensor
    d.disconnect_sensor()
    pump(0.1)
    assert not rec2.active   # recording stops as soon as disconnect starts
    sensor.join(3)
    pump(0.3)
    assert rec2.path.exists() and rec2.path != rec.path
    assert not r.start_btn.isEnabled()
    w.close()


def test_sim_calibration_tab(tmp_path):
    """Calibration tab: run with a simulated sensor → result and run history; logged to sidecar events while recording."""
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
    d.port_combo.setCurrentIndex(d.port_combo.findData("Simulated: S1813E"))
    d.connect_sensor()
    pump(1.0)
    assert c.cal_btn.isEnabled()
    r.start_btn.click()
    pump(0.3)
    c.cal_btn.click()
    assert c.busy and not c.cal_btn.isEnabled()
    c.calibrate()   # a second click while running is ignored (repeated-click lock)
    w.bench.calibrate_requested.emit()
    assert len(c.runs) == 1
    pump(1.5)
    assert c.last is not None and c.last.outcome == "ack" and c.history.count() == 1
    assert not c.busy and c.cal_btn.isEnabled()
    sent = [x for _, x in d.sensor.transport.written if x == codec.usb_set_calibration(3)]
    assert len(sent) == 1
    rows = read_history(c.history_file)
    assert len(rows) == 1 and rows[0]["info"]["port"] == "Simulated: S1813E" and rows[0]["info"]["simulated"]
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
    """Gauge panel: simulated gauge + simulated sensor → overlaid on the live plot, with lag shown."""
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
    d.port_combo.setCurrentIndex(d.port_combo.findData("Simulated: S1813E"))
    d.connect_sensor()
    gauge, sensor = g.gauge, d.sensor
    assert gauge.load == sensor.transport.load_N   # both read the same load
    pump(4.5)
    x, y = w.live.gauge_curve.getData()
    assert x is not None and len(x) > 20 and max(y) > 5
    assert len(x) % 2 == 0 and abs(x[-1]) < 0.1 and (y[0::2] == y[1::2]).all()   # steps: two points per value, ending at now
    assert g.lbl_value.text().endswith(" N") and "Gauge" in w.live.gauge_value.text()
    assert w.live.lag_value is not None and abs(w.live.lag_value) < 0.06
    d.disconnect_sensor()
    sensor.join(3)
    pump(0.3)
    assert gauge.load is None   # sensor disconnected → gauge falls back to its own load
    g.connect_btn.click()
    assert g.gauge is None and not gauge.is_alive()
    x, _ = w.live.gauge_curve.getData()
    assert x is None or len(x) == 0
    w.close()


def test_sim_bench_tab_record_analyze_results(tmp_path):
    """Test tab: simulated sensor + gauge → no-load check → calibration → record → stop → auto analysis → Results tab."""
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
    d.port_combo.setCurrentIndex(d.port_combo.findData("Simulated: S1813E"))
    d.connect_sensor()
    d.sensor.transport.period = 1.5   # press several spots within a short time
    assert pump(3.0, lambda: b.ready())
    b.label_edit.setText("A1")
    pump(0.3)
    b.noload_btn.click()
    assert pump(2.0, lambda: b.pending_events and b.pending_events[-1]["kind"] == "noload_check")
    b.cal_btn.click()
    assert pump(3.0, lambda: any(e["kind"] == "calibration" for e in b.pending_events))
    assert b.start_btn.isEnabled()
    # test points: click the drawing (P1 while preparing, P2/P3 while recording); outside the sensor = no point
    tx = b.map.geometry_.taxels
    b.pick_point(tx[14, 0], tx[14, 1])
    assert b.current_point == "P1" and b.pending_events[-1]["kind"] == "point_select"
    b.pick_point(100.0, 100.0)
    assert b.current_point is None
    b.pick_point(tx[14, 0] + 0.2, tx[14, 1])
    assert b.current_point == "P1" and len(b.point_set.points) == 1
    b.start_btn.click()
    assert b.recording and not b.label_edit.isEnabled()
    pump(3.0)
    b.pick_point(tx[3, 0], tx[3, 1])
    pump(3.0)
    b.pick_point(tx[20, 0], tx[20, 1])
    pump(3.0)
    assert [p.id for p in b.point_set.points] == ["P1", "P2", "P3"] and b.current_point == "P3"
    assert b.coverage.total_stable > 0 and "Recording" in b.lbl_rec.text()
    assert sum(b.coverage.point_stats(p)[0] for p in ("P1", "P2", "P3")) > 0
    folder = b.session.folder
    b.stop_btn.click()   # post-test no-load check (noload_check_s, still recording), then stop → analyze
    assert b.recording and not b.stop_btn.isEnabled() and b.cancel_btn.isEnabled()
    assert pump(30.0, lambda: res.result is not None)
    assert w.tabs.currentWidget() is res and res.result.folder == folder
    assert [p.id for p in res.result.points] == ["P1", "P2", "P3"]
    assert res.fig_tabs.count() == 4 and res.table.rowCount() == 1 + 3
    assert (folder / "plots" / "noload.png").is_file()
    assert (folder / "plots" / "error_map.png").is_file() and (folder / "plots" / "points.png").is_file()
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert [e["kind"] for e in meta["events"]] == ["noload_check", "calibration"] + ["point_select"] * 3 + ["noload_after"]
    assert res.result.result["noload_after"]["kind"] == "noload_after" and "After the test" in res.summary.text()
    assert meta["status"] == "stopped"
    assert (folder / "report.html").is_file() and res.sessions.count() == 1
    # re-analyze button (overwrites the same result files)
    res.result = None
    res.reanalyze_btn.click()
    assert pump(30.0, lambda: res.result is not None)
    # cancel: only the recorded files remain, no analysis
    time.sleep(1.1)
    b.start_btn.click()
    pump(1.0)
    b.cancel_btn.click()
    pump(0.5)
    assert res.sessions.count() == 2 and not (b.session.folder / "result.json").exists()
    ev = json.loads((b.session.folder / "meta.json").read_text(encoding="utf-8"))["events"]
    assert [e.get("point") for e in ev if e["kind"] == "point_select"] == ["P3"]   # the point still selected
    assert json.loads((b.session.folder / "meta.json").read_text(encoding="utf-8"))["status"] == "cancelled"
    # a new label or a new sensor connection clears the previous test's coverage / preparation events
    from paxkit.gui.bench_panel import COVERAGE_IDLE, NOLOAD_IDLE
    b.label_edit.setText("A2")
    assert b.session is None and b.lbl_cov.text() == COVERAGE_IDLE and b.lbl_rec.text() == "-"
    assert b.point_set.points == [] and b.current_point is None
    b.noload_btn.click()
    assert pump(2.0, lambda: bool(b.pending_events))
    old = d.sensor
    d.disconnect_sensor()
    old.join(3)
    d.connect_sensor()
    assert pump(3.0, lambda: b.ready())
    assert b.pending_events == [] and b.lbl_noload.text() == NOLOAD_IDLE and b.label_edit.text() == "A2"
    sensor = d.sensor
    d.disconnect_sensor()
    sensor.join(3)
    g.connect_btn.click()
    pump(0.3)
    w.close()


def test_sim_stall_notifies_and_stops_recordings(tmp_path):
    """Sensor data stall: notice box + data logging and gauge-test recordings saved up to the stall and stopped (gauge test is analyzed)."""
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
    d.port_combo.setCurrentIndex(d.port_combo.findData("Simulated: S2015"))
    d.connect_sensor()
    assert pump(3.0, lambda: b.ready())
    r.start_btn.click()
    b.label_edit.setText("B1")
    b.start_btn.click()
    assert b.recording and r.recorder.active
    pump(1.5)
    d.sensor.transport.mute = True
    assert pump(3.0, lambda: w.notice is not None)
    assert w.notice.windowTitle() == "Sensor data stalled" and "saved up to the stall" in w.notice.text()
    rec = r.recorder
    assert not rec.active and rec.path.name in w.notice.text() and "Data stalled" in r.lbl_file.text()
    side = json.loads(rec.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert [e["kind"] for e in side["events"]] == ["stalled"]
    folder = b.session.folder
    assert not b.recording and folder.name in w.notice.text()
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "stopped" and meta["events"][-1]["kind"] == "sensor_stalled"
    assert pump(30.0, lambda: res.result is not None and res.result.folder == folder)
    pump(0.3)
    assert "Data stalled" in d.lbl_status.text()
    sensor = d.sensor
    d.disconnect_sensor()
    sensor.join(3)
    g.connect_btn.click()
    pump(0.3)
    w.close()
