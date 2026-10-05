"""테스트 탭 (계획 P6-1, P7): 준비 → 무부하 확인·캘리브레이션 → 기록 → 정지(자동 분석)/취소.

- 센서·게이지 둘 다 값을 받고 있어야 기록을 시작할 수 있다.
- 무부하 확인은 기록만 한다 (meta.json 이벤트, 값 보정 없음). 캘리브레이션은 캘리브레이션 탭과 같은 동작
  (`CalibrationRun`, history.jsonl)이고 결과를 세션 이벤트에도 남긴다.
- 기록 중 화면: 게이지·센서 |F| 겹친 그래프, 현재 힘 막대(max_N 선, 넘으면 빨강), 커버리지 지도, 경과 시간·샘플 수.
- 정지하면 별도 스레드에서 분석해 `analyzed` 신호로 결과를 넘긴다. 취소는 기록 파일만 남긴다 (나중에 재분석 가능).
HAND(센서 4개, "다음 센서")는 HAND 리더(P1b) 이후에 붙인다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..bench import BenchSession, Coverage, analyze_session, load_zones, noload_check
from ..device.clock import RealClock
from . import theme
from .live_view import step_xy
from .sensor_map import SensorMap

WINDOW_S = 10.0
GUIDE = ("센서 표면의 여러 위치를 0~{max:g} N 사이의 힘으로 누르세요. 누른 채로 1초쯤 멈췄다가 힘을 천천히 바꾸고, "
         "위치를 옮겨 가며 반복하세요. 게이지 팁은 누르는 면에 수직으로 대세요. {max:g} N을 넘기지 마세요. "
         "권장 시간 3~5분, 지도의 모든 구역이 \"충분\"(초록)이 될 때까지.")


def _receiving(dev, window: float = 1.0) -> bool:
    if dev is None or not dev.is_alive() or dev.status != "connected":
        return False
    t, _ = dev.buffer.latest()
    return t is not None and dev.clock.wall() - t < window


class BenchPanel(QWidget):
    analyzed = Signal(object)        # BenchResult
    analysis_failed = Signal(str)
    calibrate_requested = Signal()
    session_saved = Signal(object)   # 세션 폴더 (분석 전)

    def __init__(self, settings: Dict[str, Any], gauge_config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.settings = dict(settings)
        self.gauge_config = dict(gauge_config or {})
        self.sensor = None
        self.sensor_info: Dict[str, Any] = {}
        self.gauge = None
        self.gauge_info: Dict[str, Any] = {}
        self.session: Optional[BenchSession] = None
        self.coverage: Optional[Coverage] = None
        self.clock = RealClock()
        self.pending_events: List[Dict[str, Any]] = []   # 준비 단계 이벤트 (기록 시작 때 세션에 넣음)
        self.calibration_busy = lambda: False   # 메인 창이 캘리브레이션 탭의 진행 중 여부로 바꾼다 (중복 클릭 잠금)
        self._noload_t0: Optional[float] = None
        self.analyzing = False
        self.root = None   # 테스트에서 저장 위치 바꾸기용 (None = data/bench)

        # ── 준비 ──
        prep = QGroupBox("1. 준비")
        pf = QFormLayout(prep)
        self.lbl_sensor = QLabel("-")
        self.lbl_gauge = QLabel("-")
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("예: A1 (폴더 이름에 들어감)")
        self.label_edit.textChanged.connect(self._update_buttons)
        pf.addRow("센서", self.lbl_sensor)
        pf.addRow("게이지", self.lbl_gauge)
        pf.addRow("라벨", self.label_edit)

        nl = QGroupBox("2. 무부하 확인 (손 뗀 상태)")
        nf = QVBoxLayout(nl)
        row = QHBoxLayout()
        self.noload_btn = QPushButton(f"무부하 확인 ({self.settings['noload_check_s']:g} s)")
        self.noload_btn.clicked.connect(self.start_noload)
        self.cal_btn = QPushButton("캘리브레이션")
        self.cal_btn.setToolTip("캘리브레이션 탭과 같은 동작 (PXSR과 같은 명령, 영점은 센서 펌웨어가 잡음)")
        self.cal_btn.clicked.connect(self.calibrate_requested)
        row.addWidget(self.noload_btn, 1)
        row.addWidget(self.cal_btn)
        self.lbl_noload = QLabel("아직 안 함 (기록만 하고 값 보정에는 쓰지 않음)")
        self.lbl_noload.setWordWrap(True)
        nf.addLayout(row)
        nf.addWidget(self.lbl_noload)

        rec = QGroupBox("3. 기록")
        rf = QVBoxLayout(rec)
        guide = QLabel(GUIDE.format(max=float(self.settings["max_N"])))
        guide.setWordWrap(True)
        row2 = QHBoxLayout()
        self.start_btn = QPushButton("기록 시작")
        self.start_btn.setProperty("primary", True)
        self.start_btn.clicked.connect(self.start_recording)
        self.stop_btn = QPushButton("정지 → 분석")
        self.stop_btn.clicked.connect(lambda: self.stop_recording(cancel=False))
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setToolTip("기록 파일만 남기고 분석하지 않음 (결과 탭에서 나중에 재분석 가능)")
        self.cancel_btn.clicked.connect(lambda: self.stop_recording(cancel=True))
        row2.addWidget(self.start_btn, 1)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.cancel_btn)
        self.lbl_rec = QLabel("-")
        self.lbl_rec.setWordWrap(True)
        rf.addWidget(guide)
        rf.addLayout(row2)
        rf.addWidget(self.lbl_rec)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(prep)
        ll.addWidget(nl)
        ll.addWidget(rec)
        ll.addStretch(1)

        # ── 기록 화면 ──
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "힘 (N)")
        self.plot.setLabel("bottom", "시간 (s)")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend()
        self.sensor_curve = self.plot.plot(pen=pg.mkPen("#5c9dff", width=2), name="센서 |F|")
        self.gauge_curve = self.plot.plot(pen=pg.mkPen("#f0f0f0", width=2), name="게이지")
        self.max_line = pg.InfiniteLine(pos=float(self.settings["max_N"]), angle=0,
                                        pen=pg.mkPen(theme.BAD, width=1, style=Qt.DashLine))
        self.plot.addItem(self.max_line)

        self.bar = pg.PlotWidget()
        self.bar.setFixedWidth(90)
        self.bar.hideAxis("bottom")
        self.bar.setMouseEnabled(False, False)
        self.bar.setMenuEnabled(False)
        self.bar.hideButtons()
        self.bar_item = pg.BarGraphItem(x=[0], height=[0], width=0.8, brush=theme.ACCENT)
        self.bar.addItem(self.bar_item)
        self.bar.addItem(pg.InfiniteLine(pos=float(self.settings["max_N"]), angle=0,
                                         pen=pg.mkPen(theme.BAD, width=2, style=Qt.DashLine)))
        self.bar.setYRange(0, float(self.settings["max_N"]) * 1.3, padding=0)
        self.bar.setXRange(-0.6, 0.6, padding=0)
        self.lbl_force = QLabel("-")
        self.lbl_force.setAlignment(Qt.AlignCenter)
        barbox = QVBoxLayout()
        barbox.addWidget(QLabel("게이지"), 0, Qt.AlignHCenter)
        barbox.addWidget(self.bar, 1)
        barbox.addWidget(self.lbl_force)

        self.map = SensorMap()
        self.map.setMinimumWidth(240)
        self.lbl_cov = QLabel("커버리지: 기록을 시작하면 표시")
        self.lbl_cov.setWordWrap(True)
        mapbox = QVBoxLayout()
        mapbox.addWidget(QLabel("커버리지 (구역별 안정 샘플 수 · 힘 구간)"))
        mapbox.addWidget(self.map, 1)
        mapbox.addWidget(self.lbl_cov)

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.plot, 1)
        tl.addLayout(barbox)
        bottom = QWidget()
        bl = QGridLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addLayout(mapbox, 0, 0)
        right = QSplitter(Qt.Vertical)
        right.addWidget(top)
        right.addWidget(bottom)
        right.setSizes([380, 420])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 650])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)
        self._n = 0
        self._update_buttons()

    # ── 장치 (메인 창에서) ──
    def set_sensor(self, sensor, info: Optional[dict] = None) -> None:
        self.sensor = sensor
        self.sensor_info = dict(info or {})
        if sensor is None and self.session is not None and self.session.status == "recording":
            self.stop_recording(cancel=False, reason="센서 연결이 끊겨 기록을 멈췄습니다")
        self._update_buttons()

    def set_gauge(self, gauge, info: Optional[dict] = None) -> None:
        self.gauge = gauge
        self.gauge_info = dict(info or {})
        if gauge is None and self.session is not None and self.session.status == "recording":
            self.stop_recording(cancel=False, reason="게이지 연결이 끊겨 기록을 멈췄습니다")
        self._update_buttons()

    def on_stalled(self, info: dict):
        """센서 수신 멈춤: 기록 중이면 멈춘 시점까지 저장하고 정지 → 분석. 저장한 세션 폴더 (기록 중이 아니면 None)."""
        if not self.recording:
            return None
        sess = self.session
        self.note_event("sensor_stalled", info.get("t"), stall_s=info.get("stall_s"))
        self.stop_recording(cancel=False, reason="센서 수신이 멈춰 기록을 멈췄습니다 (멈춘 시점까지 저장)")
        return sess.folder

    @property
    def recording(self) -> bool:
        return self.session is not None and self.session.status == "recording"

    def ready(self) -> bool:
        return _receiving(self.sensor) and _receiving(self.gauge)

    # ── 무부하 확인 ──
    def start_noload(self) -> None:
        if not self.ready() or self._noload_t0 is not None:
            return
        self._noload_t0 = self.clock.wall()
        self.lbl_noload.setText("확인 중… 센서·게이지에서 손을 떼세요")
        self._update_buttons()

    def _finish_noload(self) -> None:
        t0, t1 = self._noload_t0, self.clock.wall()
        self._noload_t0 = None
        r = noload_check(self.sensor.buffer, self.gauge.buffer, t0, t1, float(self.settings["noload_warn_N"]))
        self.note_event("noload_check", t0, **r)
        g = "-" if r["gauge_mean_N"] is None else f"{r['gauge_mean_N']:+.2f} N"
        s = "-" if r["sensor_F_mean_N"] is None else f"{r['sensor_F_mean_N']:.2f} N"
        warn = r["warnings"]
        color = theme.WARN if warn else theme.OK
        text = f"게이지 {g}, 센서 |F| {s}" + (" — " + "; ".join(warn) if warn else " — 정상")
        self.lbl_noload.setText(f"<span style='color:{color}'>{text}</span>")
        self._update_buttons()

    def note_event(self, kind: str, t: Optional[float] = None, **info) -> None:
        """준비 중이면 모아 두고, 기록 중이면 세션에 바로 남긴다."""
        t = time.time() if t is None else t
        if self.recording:
            self.session.note_event(kind, t, **info)
        else:
            self.pending_events.append({"kind": kind, "t": t, **info})

    def note_calibration(self, result) -> None:
        """캘리브레이션 탭(또는 이 탭 버튼)에서 1회가 끝나면."""
        d = result.to_dict()
        d.pop("requested", None)
        self.note_event("calibration", result.requested, **d)

    # ── 기록 ──
    def start_recording(self) -> None:
        if self.recording or not self.ready() or not self.label_edit.text().strip():
            return
        s = self.sensor
        model = s.sensor_type.label
        sess = BenchSession(s, self.gauge, self.settings, label=self.label_edit.text().strip(), model=model,
                            sensor_info=self.sensor_info, gauge_info=self.gauge_info,
                            gauge_config=self.gauge_config, root=self.root)
        for e in self.pending_events:
            e = dict(e)
            kind, t = e.pop("kind"), e.pop("t")
            sess.note_event(kind, t, **e)
        self.pending_events = []
        zs = load_zones(model)
        self.coverage = Coverage(zs, self.settings)
        self.map.set_model(model)
        sess.start()
        s.add_sink(self.coverage.add_frame)
        self.gauge.add_sink(self.coverage.add_gauge)
        self.session = sess
        self._update_buttons()

    def stop_recording(self, cancel: bool = False, reason: str = "") -> None:
        sess = self.session
        if sess is None or sess.status != "recording":
            return
        cov = self.coverage
        if self.sensor is not None and cov is not None:
            self.sensor.remove_sink(cov.add_frame)
        if self.gauge is not None and cov is not None:
            self.gauge.remove_sink(cov.add_gauge)
        if sess.gauge is not None and cov is not None:
            sess.gauge.remove_sink(cov.add_gauge)
        sess.stop(cancelled=cancel)
        self.session_saved.emit(sess.folder)
        msg = (reason + ". " if reason else "") + f"저장: {sess.folder.name}"
        if cancel or sess.sensor_csv is None:
            self.lbl_rec.setText(msg + (" (취소 — 분석 안 함)" if cancel else " (센서 기록 없음 — 분석 안 함)"))
        else:
            self.lbl_rec.setText(msg + " — 분석 중…")
            self._analyze(sess.folder)
        self._update_buttons()

    def _analyze(self, folder) -> None:
        self.analyzing = True

        def work():
            try:
                res = analyze_session(folder)
            except Exception as e:   # 분석 실패해도 기록 파일은 남아 있음
                self.analyzing = False
                self.analysis_failed.emit(f"{folder.name}: {e}")
                return
            self.analyzing = False
            self.analyzed.emit(res)

        threading.Thread(target=work, daemon=True, name="BenchAnalyze").start()

    def analysis_done(self, res) -> None:
        self.lbl_rec.setText(f"분석 완료: {res.folder.name} (결과 탭)")
        self._update_buttons()

    # ── 화면 ──
    def _update_buttons(self) -> None:
        rec = self.recording
        ready = self.ready()
        busy = self._noload_t0 is not None
        self.noload_btn.setEnabled(ready and not rec and not busy)
        self.cal_btn.setEnabled(self.sensor is not None and _receiving(self.sensor) and not busy
                                and not self.calibration_busy())
        self.start_btn.setEnabled(ready and not rec and not busy and bool(self.label_edit.text().strip()))
        self.stop_btn.setEnabled(rec)
        self.cancel_btn.setEnabled(rec)
        self.label_edit.setEnabled(not rec)

    def _tick(self) -> None:
        self._n += 1
        now = self.clock.wall()
        s, g = self.sensor, self.gauge
        if s is not None:
            ok = _receiving(s)
            self.lbl_sensor.setText(f"{s.sensor_type.label}, {s.buffer.rate(now):.0f} Hz" if ok else "값 없음")
        else:
            self.lbl_sensor.setText("연결 안 됨 (왼쪽 센서 패널)")
        if g is not None:
            ok = _receiving(g)
            v = g.latest()
            self.lbl_gauge.setText(f"{g.port}, {g.buffer.rate(now):.0f} Hz, {v:.1f} N" if ok and v is not None else "값 없음")
        else:
            self.lbl_gauge.setText("연결 안 됨 (왼쪽 Force gauge 패널)")
        if self._noload_t0 is not None and now - self._noload_t0 >= float(self.settings["noload_check_s"]):
            self._finish_noload()
        if self._n % 5 == 0:
            self._update_buttons()
        self._draw(now)
        if self.recording and self._n % 3 == 0:
            self._draw_coverage(now)

    def _draw(self, now: float) -> None:
        s, g = self.sensor, self.gauge
        if s is not None:
            t, v = s.buffer.snapshot(since=now - WINDOW_S)
            if t.size:
                self.sensor_curve.setData(t - now, np.linalg.norm(v, axis=1) / 10.0)
        else:
            self.sensor_curve.setData([], [])
        gv = None
        if g is not None:
            t, v = g.buffer.snapshot(since=now - WINDOW_S)
            if t.size:
                self.gauge_curve.setData(*step_xy(t - now, v[:, 0], 0.0))
                gv = float(v[-1, 0])
        else:
            self.gauge_curve.setData([], [])
        self.plot.setXRange(-WINDOW_S, 0, padding=0)
        mx = float(self.settings["max_N"])
        h = max(gv or 0.0, 0.0)
        self.bar_item.setOpts(height=[min(h, mx * 1.3)], brush=theme.BAD if h > mx else theme.ACCENT)
        self.lbl_force.setText("-" if gv is None else f"{gv:.1f} N")

    def _draw_coverage(self, now: float) -> None:
        cov, sess = self.coverage, self.session
        cov.update()
        elapsed = now - (sess.started_at or now)
        self.lbl_rec.setText(f"기록 중 {int(elapsed // 60)}:{int(elapsed % 60):02d} — 센서 {sess.sensor_rows}행, "
                             f"게이지 {sess.gauge_rows}개 → {sess.folder.name}")
        zs = cov.zones
        if zs is None:
            self.lbl_cov.setText(f"안정 샘플 {cov.total_stable} (이 센서는 구역 정의 없음)")
            return
        enough = cov.enough()
        need = int(self.settings["zone_min_samples"])
        colors, labels = [], []
        for k, z in enumerate(zs.zones):
            n = int(cov.stable[k])
            filled = "".join("■" if c > 0 else "□" for c in cov.bin_counts[k])
            colors.append(theme.OK if enough[k] else (theme.WARN if n > 0 else "#5a5c62"))
            labels.append(f"{n}\n{filled}")
        self.map.show_zones(colors, labels)
        b = cov.bins
        self.lbl_cov.setText(
            f"안정 샘플 {cov.total_stable} (접촉 {cov.total_contact}, 위치 없음 {cov.no_zone}) · "
            f"충분 {int(enough.sum())}/{len(enough)} 구역 (안정 {need}개 이상 + 힘 구간 2개 이상) · "
            f"■ = 힘 구간 {b[0]:g}–{b[1]:g} / {b[1]:g}–{b[2]:g} / {b[2]:g} N 이상")
