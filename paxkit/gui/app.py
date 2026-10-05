from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from ..config import Config
from . import theme
from .main_window import MainWindow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Paxini Gen3 도구 (PXSR 대체 + force gauge 테스트)")
    ap.add_argument("--config", help="config.yaml 경로 (기본: 레포 루트)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.load(a.config)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("paxkit")
    theme.apply(app)
    w = MainWindow(cfg)
    w.show()
    return app.exec()
