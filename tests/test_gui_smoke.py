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
