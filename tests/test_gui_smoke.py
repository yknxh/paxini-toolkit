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
