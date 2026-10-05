"""결과 탭 (계획 P6-5 6, P7): 게이지 테스트 결과 보기 + 지난 세션 목록 + 로깅 파일 목록.

그림은 분석 결과 파일(`result.json`, `samples.csv`)에서 어두운 테마로 다시 그린다 (파일의 PNG는 밝은 테마, 내용은 같음).
재분석은 기록 파일만 읽어 결과를 덮어쓴다 (`tools/bench_analyze.py`와 같은 코드).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..bench import BenchResult, analyze_session, load_result
from ..bench.analyze import read_meta
from ..bench.plots import lag_text
from ..bench.report import METRIC_LABELS, figures
from ..paths import data_path
from . import theme

STATUS_TEXT = {"stopped": "", "cancelled": " · 취소됨", "recording": " · 기록 중/중단됨"}


def list_sessions(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if (p / "meta.json").is_file()), reverse=True)


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "충분" if v else "부족"
    if isinstance(v, float):
        return "-" if v != v else f"{v:.3f}"
    return str(v)


class BenchResults(QWidget):
    _reanalyzed = Signal(object)
    _failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.root: Optional[Path] = None      # None = data/bench
        self.logs_root: Optional[Path] = None  # None = data/logs
        self.result: Optional[BenchResult] = None
        self.canvases: List[FigureCanvasQTAgg] = []

        self.sessions = QListWidget()
        self.sessions.currentItemChanged.connect(lambda cur, _prev: self._select(cur))
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh)
        self.reanalyze_btn = QPushButton("재분석")
        self.reanalyze_btn.clicked.connect(self.reanalyze)
        self.folder_btn = QPushButton("폴더 열기")
        self.folder_btn.clicked.connect(lambda: self._open(self._current()))
        self.report_btn = QPushButton("report.html")
        self.report_btn.clicked.connect(lambda: self._open(self._current() / "report.html" if self._current() else None))
        b1 = QHBoxLayout()
        b1.addWidget(refresh)
        b1.addWidget(self.reanalyze_btn)
        b2 = QHBoxLayout()
        b2.addWidget(self.folder_btn)
        b2.addWidget(self.report_btn)
        self.logs = QListWidget()
        self.logs.setToolTip("더블클릭: 폴더 열기")
        self.logs.itemDoubleClicked.connect(lambda it: self._open(Path(it.data(Qt.UserRole)).parent))
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("게이지 테스트 세션 (data/bench)"))
        ll.addWidget(self.sessions, 3)
        ll.addLayout(b1)
        ll.addLayout(b2)
        ll.addWidget(QLabel("데이터 로깅 파일 (data/logs)"))
        ll.addWidget(self.logs, 1)

        self.summary = QLabel("세션을 고르세요")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        self.table = QTableWidget(0, len(METRIC_LABELS))
        self.table.setHorizontalHeaderLabels([lab for _, lab in METRIC_LABELS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.fig_tabs = QTabWidget()
        rsplit = QSplitter(Qt.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.summary)
        tl.addWidget(self.table, 1)
        rsplit.addWidget(top)
        rsplit.addWidget(self.fig_tabs)
        rsplit.setSizes([260, 560])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(rsplit)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 900])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self._reanalyzed.connect(self.show_result)
        self._failed.connect(lambda m: self.summary.setText(f"<span style='color:{theme.BAD}'>분석 실패: {m}</span>"))
        self.refresh()

    # ── 목록 ──
    def _root(self) -> Path:
        return self.root or data_path("bench")

    def refresh(self, select: Optional[Path] = None) -> None:
        cur = select or self._current()
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for p in list_sessions(self._root()):
            meta = read_meta(p)
            done = (p / "result.json").is_file()
            text = p.name + STATUS_TEXT.get(meta.get("status", ""), "") + ("" if done else " · 미분석")
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, str(p))
            if not done:
                it.setForeground(QColor(theme.MUTED))
            self.sessions.addItem(it)
            if cur is not None and Path(cur) == p:
                self.sessions.setCurrentItem(it)
        self.sessions.blockSignals(False)
        self.logs.clear()
        logs = self.logs_root or data_path("logs")
        for p in sorted(logs.glob("*.csv"), reverse=True) if logs.is_dir() else []:
            it = QListWidgetItem(f"{p.name}  ({p.stat().st_size // 1024} KB)")
            it.setData(Qt.UserRole, str(p))
            self.logs.addItem(it)
        self._update_buttons()

    def _current(self) -> Optional[Path]:
        it = self.sessions.currentItem()
        return Path(it.data(Qt.UserRole)) if it is not None else None

    def _select(self, item) -> None:
        self._update_buttons()
        if item is None:
            return
        folder = Path(item.data(Qt.UserRole))
        res = load_result(folder)
        if res is None:
            self._clear()
            meta = read_meta(folder)
            self.summary.setText(f"<b>{folder.name}</b><br>분석 결과 없음 (상태 {meta.get('status', '-')}). "
                                 "\"재분석\"으로 기록 파일을 분석합니다.")
            return
        self.show_result(res, refresh=False)

    def _update_buttons(self) -> None:
        cur = self._current()
        self.reanalyze_btn.setEnabled(cur is not None)
        self.folder_btn.setEnabled(cur is not None)
        self.report_btn.setEnabled(cur is not None and (cur / "report.html").is_file())

    # ── 보기 ──
    def _clear(self) -> None:
        self.result = None
        self.table.setRowCount(0)
        while self.fig_tabs.count():
            w = self.fig_tabs.widget(0)
            self.fig_tabs.removeTab(0)
            w.deleteLater()
        self.canvases = []

    def show_result(self, res: BenchResult, refresh: bool = True) -> None:
        if refresh:
            self.refresh(select=res.folder)
        self._clear()
        self.result = res
        r = res.result
        c = r["counts"]
        sens = "; ".join(
            f"{s['label']} {s.get('model')} {s['rate_hz']} Hz, 남은 지연 {lag_text(s)}" for s in r["sensors"])
        warn = "".join(f"<li>{w}</li>" for w in r["warnings"])
        nl = r.get("noload_check") or {}
        m = res.metrics.iloc[0].to_dict() if len(res.metrics) else {}
        self.summary.setText(
            f"<b>{res.folder.name}</b> — 기록 {r.get('duration_s')} s, 게이지 샘플 {c['gauge_samples']}, "
            f"안정 {c['stable']}, 접촉 {c['contact']}<br>{sens}<br>"
            f"<b>전체 (안정 샘플): bias {_fmt(m.get('bias_N'))} N, RMSE {_fmt(m.get('rmse_N'))} N "
            f"({_fmt(m.get('rmse_pct_fs'))} %FS), 기울기 {_fmt(m.get('slope'))}, R² {_fmt(m.get('r2'))}</b> "
            f"— 합격/불합격 판정 없음<br>"
            + (f"무부하 확인: 게이지 {nl.get('gauge_mean_N')} N, 센서 |F| {nl.get('sensor_F_mean_N')} N<br>" if nl else
               "무부하 확인: 기록 없음<br>")
            + (f"<span style='color:{theme.WARN}'>경고</span><ul style='margin:0'>{warn}</ul>" if warn else ""))
        self._fill_table(res)
        for rel, title, fig in figures(res, "dark"):
            canvas = FigureCanvasQTAgg(fig)
            w, h = fig.get_size_inches() * fig.dpi
            canvas.setMinimumSize(int(w * 0.55), int(h * 0.7))   # 폭은 화면에 맞추고, 작으면 스크롤
            area = QScrollArea()
            area.setWidget(canvas)
            area.setWidgetResizable(True)
            self.fig_tabs.addTab(area, title)
            self.canvases.append(canvas)
        self._update_buttons()

    def _fill_table(self, res: BenchResult) -> None:
        rows = res.metrics.to_dict(orient="records")
        self.table.setRowCount(len(rows))
        multi = len(res.sensors) > 1
        for i, r in enumerate(rows):
            thin = r["scope"] == "zone" and not bool(r.get("enough"))
            for j, (k, _) in enumerate(METRIC_LABELS):
                if k == "name":
                    v = (f"{r['sensor']} / " if multi and r["scope"] == "zone" else "") + str(r["name"])
                else:
                    v = r.get(k)
                    v = None if isinstance(v, float) and v != v else v
                it = QTableWidgetItem(_fmt(v))
                it.setTextAlignment(Qt.AlignVCenter | (Qt.AlignLeft if k == "name" else Qt.AlignRight))
                if thin:
                    it.setForeground(QColor(theme.MUTED))
                if r["scope"] != "zone":
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    # ── 버튼 ──
    def reanalyze(self) -> None:
        folder = self._current()
        if folder is None:
            return
        self.summary.setText(f"<b>{folder.name}</b> 분석 중…")
        self.reanalyze_btn.setEnabled(False)

        def work():
            try:
                self._reanalyzed.emit(analyze_session(folder))
            except Exception as e:
                self._failed.emit(f"{folder.name}: {e}")

        threading.Thread(target=work, daemon=True, name="BenchReanalyze").start()

    def _open(self, path: Optional[Path]) -> None:
        if path is not None and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
