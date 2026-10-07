"""Test tab (plan P6-1, P7): prepare → no-load check/calibration → record → stop (auto analysis)/cancel.

- Recording can start only while both sensor and gauge are delivering values.
- The no-load check is only logged (meta.json event, no value correction). Calibration is the same action as the Calibration tab
  (`CalibrationRun`, history.jsonl), and its result is also logged as a session event.
- Recording view: gauge and sensor |F| overlaid plot, current force bar (max_N line, red above it), test point map, elapsed time and sample counts.
- Test points (default way to test, 2026-10-06): click a spot on the sensor drawing, then press that spot. Each selection is a
  `point_select` event; the analysis locates samples at the selected point (`bench/points.py`). Clicking outside the sensor = no point.
- Stop button: a post-test no-load check first (hands off for `noload_check_s`, still recording, `noload_after` event),
  then the recording stops. Cancel, or a stop caused by a stall/disconnect, stops at once without it.
- On stop, analysis runs in a separate thread and the result is passed via the `analyzed` signal. Cancel keeps only the recorded files (can be re-analyzed later).
HAND (4 sensors, "next sensor") will be added after the HAND reader (P1b).
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

from ..bench import NOLOAD_AFTER_EVENT, BenchSession, Coverage, analyze_session, noload_check
from ..bench.points import SELECT_EVENT, PointSet, select_event
from ..device.geometry import load_geometry
from ..device.clock import RealClock
from . import theme
from .light_grid import LightGrid
from .live_view import step_xy
from .sensor_map import SensorMap

WINDOW_S = 10.0
GUIDE = ("Click a spot on the sensor drawing (right), then press exactly that spot: hold the force still for about 1 s at "
         "several levels spread over 0–{max:g} N (e.g. 2, 5, 8, 12 N), release, and repeat once or twice. Then click the next "
         "spot. Keep the gauge tip perpendicular to the pressed surface and do not exceed {max:g} N. Spread the points over the "
         "surface; a point needs ≥{n} stable samples (green). Clicking outside the sensor = no point (presses are not located).")


def _receiving(dev, window: float = 1.0) -> bool:
    if dev is None or not dev.is_alive() or dev.status != "connected":
        return False
    t, _ = dev.buffer.latest()
    return t is not None and dev.clock.wall() - t < window

NOLOAD_IDLE = "Not done yet (logged only; not used to correct values)"
COVERAGE_IDLE = "Click the drawing to choose where you press"
POINT_IDLE = "#5a5c62"


class BenchPanel(QWidget):
    analyzed = Signal(object)        # BenchResult
    analysis_failed = Signal(str)
    calibrate_requested = Signal()
    session_saved = Signal(object)   # session folder (before analysis)

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
        self.pending_events: List[Dict[str, Any]] = []   # preparation events (added to the session when recording starts)
        self.calibration_busy = lambda: False   # the main window replaces this with the Calibration tab's busy state (repeated-click lock)
        self._noload_t0: Optional[float] = None
        self._after_t0: Optional[float] = None   # post-test no-load check running (Stop pressed)
        self._last_sensor = None   # last connected reader (a different one = new connection)
        self.analyzing = False
        self.root = None   # lets tests change the save location (None = data/bench)
        self.point_set: Optional[PointSet] = None
        self.current_point: Optional[str] = None

        # ── preparation ──
        prep = QGroupBox("1. Prepare")
        pf = QFormLayout(prep)
        self.lbl_sensor = QLabel("-")
        self.lbl_gauge = QLabel("-")
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("e.g. A1 (used in the folder name)")
        self.label_edit.textChanged.connect(self._on_label_changed)
        pf.addRow("Sensor", self.lbl_sensor)
        pf.addRow("Gauge", self.lbl_gauge)
        pf.addRow("Label", self.label_edit)

        nl = QGroupBox("2. No-load check (hands off)")
        nf = QVBoxLayout(nl)
        row = QHBoxLayout()
        self.noload_btn = QPushButton(f"No-load check ({self.settings['noload_check_s']:g} s)")
        self.noload_btn.clicked.connect(self.start_noload)
        self.cal_btn = QPushButton("Calibrate")
        self.cal_btn.setToolTip("Same as the Calibration tab (same command as PXSR; the sensor firmware sets the zero)")
        self.cal_btn.clicked.connect(self.calibrate_requested)
        row.addWidget(self.noload_btn, 1)
        row.addWidget(self.cal_btn)
        self.lbl_noload = QLabel(NOLOAD_IDLE)
        self.lbl_noload.setWordWrap(True)
        nf.addLayout(row)
        nf.addWidget(self.lbl_noload)

        rec = QGroupBox("3. Record")
        rf = QVBoxLayout(rec)
        guide = QLabel(GUIDE.format(max=float(self.settings["max_N"]), n=int(self.settings["point_min_samples"])))
        guide.setWordWrap(True)
        row2 = QHBoxLayout()
        self.start_btn = QPushButton("Start recording")
        self.start_btn.setProperty("primary", True)
        self.start_btn.clicked.connect(self.start_recording)
        self.stop_btn = QPushButton("Stop → analyze")
        self.stop_btn.setToolTip("Hands off for a few seconds (post-test no-load check), then stop and analyze")
        self.stop_btn.clicked.connect(self.request_stop)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Keep the recorded files without analyzing (can be re-analyzed later from the Results tab)")
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

        # ── recording view ──
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Force (N)")
        self.plot.setLabel("bottom", "Time (s)")
        self.grid = LightGrid(self.plot, x_ticks=range(-int(WINDOW_S), 1))   # showGrid is slow (light_grid.py)
        self.plot.addLegend()
        self.sensor_curve = self.plot.plot(pen=pg.mkPen("#5c9dff", width=2), name="Sensor |F|")
        self.gauge_curve = self.plot.plot(pen=pg.mkPen("#f0f0f0", width=2), name="Gauge")
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
        barbox.addWidget(QLabel("Gauge"), 0, Qt.AlignHCenter)
        barbox.addWidget(self.bar, 1)
        barbox.addWidget(self.lbl_force)

        self.map = SensorMap()
        self.map.setMinimumWidth(240)
        self.map.clicked.connect(self.pick_point)
        self.lbl_cov = QLabel(COVERAGE_IDLE)
        self.lbl_cov.setWordWrap(True)
        mapbox = QVBoxLayout()
        mapbox.addWidget(QLabel("Test points (click = press here next; label = stable samples · mean |error| %)"))
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

    # ── devices (from the main window) ──
    def set_sensor(self, sensor, info: Optional[dict] = None) -> None:
        self.sensor = sensor
        self.sensor_info = dict(info or {})
        if sensor is None and self.session is not None and self.session.status == "recording":
            self.stop_recording(cancel=False, reason="Recording stopped: sensor disconnected")
        if sensor is not None and sensor is not self._last_sensor and not self.recording:
            # a new connection (another sensor on the same port is also a new reader object): the previous
            # sensor's no-load check / calibration events and coverage belong to that sensor, not this one
            self.pending_events = []
            self._noload_t0 = None
            self.lbl_noload.setText(NOLOAD_IDLE)
            self._clear_previous()
            self._reset_points()
        if sensor is not None:
            self._last_sensor = sensor
            self._sync_model()
        self._update_buttons()

    def _clear_previous(self) -> None:
        """Clear the finished test's points, map and status (files and the Results tab are untouched)."""
        if self.recording:
            return
        if self.session is not None:   # a finished test's points belong to it; points chosen while preparing are kept
            self._reset_points()
        self.session = None
        self.coverage = None
        self._draw_points()
        if not self.analyzing:
            self.lbl_rec.setText("-")

    # ── test points ──
    def _sync_model(self) -> None:
        """Drawing and point set follow the connected sensor's model (a model change drops the points)."""
        s = self.sensor
        if s is None or self.recording:
            return
        model = s.sensor_type.label
        if self.map.model != model or self.point_set is None:
            self.map.set_model(model)
            g = load_geometry(model)
            self.point_set = PointSet(g) if g is not None else None
            self.current_point = None
            self.pending_events = [e for e in self.pending_events if e["kind"] != SELECT_EVENT]
            self._draw_points()

    def _reset_points(self) -> None:
        if self.point_set is not None:
            self.point_set = PointSet(self.point_set.geometry)
        self.current_point = None
        self.pending_events = [e for e in self.pending_events if e["kind"] != SELECT_EVENT]
        self._draw_points()

    def pick_point(self, x: float, y: float) -> None:
        """Map click: select (or create) the point at (x, y); outside the sensor = no point."""
        ps = self.point_set
        if ps is None or self.analyzing:
            return
        if self.session is not None and not self.recording:
            self._clear_previous()   # the previous test is done: clicking starts the next one
            ps = self.point_set
        p = ps.pick(x, y)
        pid = p.id if p is not None else None
        if pid == self.current_point:
            return
        self.current_point = pid
        t = self.clock.wall()
        self.note_event(SELECT_EVENT, t, **select_event(p))
        if self.recording and self.coverage is not None:
            self.coverage.select_point(t, pid)
        self._draw_points()
        self._update_buttons()

    def _draw_points(self) -> None:
        ps = self.point_set
        if ps is None:
            self.map.show_points([])
            return
        cov = self.coverage
        need = int(self.settings["point_min_samples"])
        items = []
        for p in ps.points:
            _, n, pct = cov.point_stats(p.id) if cov is not None else (0, 0, None)
            color = theme.OK if n >= need else (theme.WARN if n > 0 else POINT_IDLE)
            text = p.id if cov is None else f"{p.id} {n}" + ("" if pct is None else f" · {pct:.0f}%")
            items.append((p.x_mm, p.y_mm, text, color))
        cur = ps.get(self.current_point)
        self.map.show_points(items, (cur.x_mm, cur.y_mm) if cur is not None else None)
        if not self.recording and cov is None and not ps.points:
            self.lbl_cov.setText(COVERAGE_IDLE)
        elif not self.recording and cov is None:
            self.lbl_cov.setText(f"{len(ps.points)} points. Next press: "
                                 + (f"{cur.id} ({cur.x_mm:.1f}, {cur.y_mm:.1f} mm)" if cur else
                                    "no point selected — click the drawing"))

    def _on_label_changed(self, _text: str) -> None:
        if self.session is not None and not self.recording:
            self._clear_previous()   # a new label = a new test
        self._update_buttons()

    def set_gauge(self, gauge, info: Optional[dict] = None) -> None:
        self.gauge = gauge
        self.gauge_info = dict(info or {})
        if gauge is None and self.session is not None and self.session.status == "recording":
            self.stop_recording(cancel=False, reason="Recording stopped: gauge disconnected")
        self._update_buttons()

    def on_stalled(self, info: dict):
        """Sensor data stalled: if recording, save up to the stall and stop → analyze. Returns the saved session folder (None if not recording)."""
        if not self.recording:
            return None
        sess = self.session
        self.note_event("sensor_stalled", info.get("t"), stall_s=info.get("stall_s"))
        self.stop_recording(cancel=False, reason="Recording stopped: sensor data stalled (saved up to the stall)")
        return sess.folder

    @property
    def recording(self) -> bool:
        return self.session is not None and self.session.status == "recording"

    def ready(self) -> bool:
        return _receiving(self.sensor) and _receiving(self.gauge)

    # ── no-load check ──
    def start_noload(self) -> None:
        if not self.ready() or self._noload_t0 is not None:
            return
        self._noload_t0 = self.clock.wall()
        self.lbl_noload.setText("Checking… keep hands off the sensor and gauge")
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
        text = f"Gauge {g}, sensor |F| {s}" + (" — " + "; ".join(warn) if warn else " — OK")
        self.lbl_noload.setText(f"<span style='color:{color}'>{text}</span>")
        self._update_buttons()

    def note_event(self, kind: str, t: Optional[float] = None, **info) -> None:
        """Collect while preparing; log straight to the session while recording."""
        t = time.time() if t is None else t
        if self.recording:
            self.session.note_event(kind, t, **info)
        else:
            self.pending_events.append({"kind": kind, "t": t, **info})

    def note_calibration(self, result) -> None:
        """When one calibration run finishes in the Calibration tab (or via this tab's button)."""
        d = result.to_dict()
        d.pop("requested", None)
        self.note_event("calibration", result.requested, **d)

    # ── recording ──
    def start_recording(self) -> None:
        if self.recording or not self.ready() or not self.label_edit.text().strip():
            return
        s = self.sensor
        model = s.sensor_type.label
        sess = BenchSession(s, self.gauge, self.settings, label=self.label_edit.text().strip(), model=model,
                            sensor_info=self.sensor_info, gauge_info=self.gauge_info,
                            gauge_config=self.gauge_config, root=self.root)
        # point selections while preparing only matter through the point selected at the start (written below)
        for e in self.pending_events:
            if e["kind"] == SELECT_EVENT:
                continue
            e = dict(e)
            kind, t = e.pop("kind"), e.pop("t")
            sess.note_event(kind, t, **e)
        self.pending_events = []
        self.coverage = Coverage(self.settings)
        self._sync_model()
        cur = self.point_set.get(self.current_point) if self.point_set is not None else None
        if cur is not None:   # selected before the start (also kept from the previous recording): holds from the start
            t = time.time()
            sess.note_event(SELECT_EVENT, t, **select_event(cur))
            self.coverage.select_point(t, cur.id)
        sess.start()
        s.add_sink(self.coverage.add_frame)
        self.gauge.add_sink(self.coverage.add_gauge)
        self.session = sess
        self._update_buttons()

    def request_stop(self) -> None:
        """Stop button: post-test no-load check (hands off, still recording), then stop → analyze (`_finish_after`).
        Stops at once if the sensor or gauge is not delivering values."""
        if not self.recording or self._after_t0 is not None:
            return
        if not self.ready():
            self.stop_recording(cancel=False)
            return
        self._after_t0 = self.clock.wall()
        self._update_buttons()

    def _finish_after(self) -> None:
        t0, t1 = self._after_t0, self.clock.wall()
        self._after_t0 = None
        if not self.recording:
            return
        r = noload_check(self.sensor.buffer, self.gauge.buffer, t0, t1, float(self.settings["noload_warn_N"]))
        self.note_event(NOLOAD_AFTER_EVENT, t0, **r)
        g = "-" if r["gauge_mean_N"] is None else f"{r['gauge_mean_N']:+.2f} N"
        s = "-" if r["sensor_F_mean_N"] is None else f"{r['sensor_F_mean_N']:.2f} N"
        warn = r["warnings"]
        color = theme.WARN if warn else theme.OK
        text = f"After the test: gauge {g}, sensor |F| {s}" + (" — " + "; ".join(warn) if warn else " — OK")
        self.lbl_noload.setText(f"<span style='color:{color}'>{text}</span>")
        self.stop_recording(cancel=False)

    def stop_recording(self, cancel: bool = False, reason: str = "") -> None:
        sess = self.session
        if sess is None or sess.status != "recording":
            return
        self._after_t0 = None   # cancel / forced stop during the post-test check: stop without it
        cov = self.coverage
        if self.sensor is not None and cov is not None:
            self.sensor.remove_sink(cov.add_frame)
        if self.gauge is not None and cov is not None:
            self.gauge.remove_sink(cov.add_gauge)
        if sess.gauge is not None and cov is not None:
            sess.gauge.remove_sink(cov.add_gauge)
        sess.stop(cancelled=cancel)
        self.session_saved.emit(sess.folder)
        msg = (reason + ". " if reason else "") + f"Saved: {sess.folder.name}"
        if cancel or sess.sensor_csv is None:
            self.lbl_rec.setText(msg + (" (cancelled — not analyzed)" if cancel else " (no sensor data — not analyzed)"))
        else:
            self.lbl_rec.setText(msg + " — analyzing…")
            self._analyze(sess.folder)
        self._update_buttons()

    def _analyze(self, folder) -> None:
        self.analyzing = True

        def work():
            try:
                res = analyze_session(folder)
            except Exception as e:   # the recorded files remain even if analysis fails
                self.analyzing = False
                self.analysis_failed.emit(f"{folder.name}: {e}")
                return
            self.analyzing = False
            self.analyzed.emit(res)

        threading.Thread(target=work, daemon=True, name="BenchAnalyze").start()

    def analysis_done(self, res) -> None:
        self.lbl_rec.setText(f"Analysis done: {res.folder.name} (Results tab)")
        self._update_buttons()

    # ── display ──
    def _update_buttons(self) -> None:
        rec = self.recording
        ready = self.ready()
        busy = self._noload_t0 is not None
        self.noload_btn.setEnabled(ready and not rec and not busy)
        self.cal_btn.setEnabled(self.sensor is not None and _receiving(self.sensor) and not busy
                                and not self.calibration_busy())
        self.start_btn.setEnabled(ready and not rec and not busy and bool(self.label_edit.text().strip()))
        self.stop_btn.setEnabled(rec and self._after_t0 is None)
        self.cancel_btn.setEnabled(rec)
        self.label_edit.setEnabled(not rec)

    def _tick(self) -> None:
        self._n += 1
        now = self.clock.wall()
        s, g = self.sensor, self.gauge
        if s is not None and self._n % 10 == 0 and self.session is None:
            self._sync_model()   # the sensor type is confirmed after connecting
        if s is not None:
            ok = _receiving(s)
            self.lbl_sensor.setText(f"{s.sensor_type.label}, {s.buffer.rate(now):.0f} Hz" if ok else "No values")
        else:
            self.lbl_sensor.setText("Not connected (sensor panel, left)")
        if g is not None:
            ok = _receiving(g)
            v = g.latest()
            self.lbl_gauge.setText(f"{g.port}, {g.buffer.rate(now):.0f} Hz, {v:.1f} N" if ok and v is not None else "No values")
        else:
            self.lbl_gauge.setText("Not connected (Force gauge panel, left)")
        if self._noload_t0 is not None and now - self._noload_t0 >= float(self.settings["noload_check_s"]):
            self._finish_noload()
        if self._after_t0 is not None and now - self._after_t0 >= float(self.settings["noload_check_s"]):
            self._finish_after()
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
        if self._after_t0 is not None:
            left = max(float(self.settings["noload_check_s"]) - (now - self._after_t0), 0.0)
            self.lbl_rec.setText(f"<span style='color:{theme.WARN}'>Post-test no-load check: take your hands off the "
                                 f"sensor and gauge… {left:.1f} s</span> (then the recording stops)")
        else:
            self.lbl_rec.setText(f"Recording {int(elapsed // 60)}:{int(elapsed % 60):02d} — sensor {sess.sensor_rows} rows, "
                                 f"gauge {sess.gauge_rows} samples → {sess.folder.name}")
        self._draw_points()
        ps = self.point_set
        need = int(self.settings["point_min_samples"])
        done = sum(cov.point_stats(p.id)[1] >= need for p in ps.points) if ps is not None else 0
        cur = ps.get(self.current_point) if ps is not None else None
        if cur is not None:
            n, nst, pct = cov.point_stats(cur.id)
            now_txt = (f"pressing {cur.id}: contact {n}, stable {nst}"
                       + ("" if pct is None else f", sensitivity error {pct:+.1f}% (before lag correction)"))
        else:
            now_txt = "<span style='color:%s'>no point selected — presses are not located; click the drawing</span>" % theme.WARN
        self.lbl_cov.setText(f"{now_txt}<br>{len(ps.points) if ps else 0} points, {done} with ≥{need} stable samples · "
                             f"contact {cov.total_contact}, stable {cov.total_stable}")
