"""Gauge test (plan P6-6 verification): point model, synthetic session analysis, re-analysis determinism, coverage, session recording.
Test points (location, sensitivity, error map) are in test_bench_points.py."""
import json
import time
from pathlib import Path

import numpy as np
import pytest

from bench_synth import START, SynthSensor, make_session
from paxkit.bench import BenchSession, Coverage, analyze_session, bench_settings, load_result, noload_check
from paxkit.bench.pairing import SensorSeries, pair_sensor, window_slopes
from paxkit.buffers import TimeSeriesBuffer
from paxkit.device.frames import Frame
from paxkit.device.geometry import load_geometry
from paxkit.device.sim import SimUsbTransport
from paxkit.device.usb import UsbSensor
from paxkit.gauge import SimGauge
from paxkit.recording import pxsr_csv
from paxkit.recording.reader import read_log

PAXTEST_GEOM = Path(__file__).resolve().parents[2] / "paxini-test-windows/paxtest/devices/geometry"


# ── point model ──
@pytest.mark.parametrize("label,old,n,m", [("S1813E", "S1813E", 31, 660), ("S2015", "S2015E", 52, 1156)])
def test_geometry_matches_paxtest(label, old, n, m):
    g = load_geometry(label)
    assert g.taxels.shape == (n, 3) and g.surface.shape == (m, 3) and g.neighbor.max() < n
    assert load_geometry(old) is g   # the config name S2015E maps to the same model
    p = PAXTEST_GEOM / f"{old}.json"
    if not p.is_file():
        pytest.skip("paxtest geometry not found")
    ref = np.array(json.loads(p.read_text(encoding="utf-8"))["positions_mm"])
    assert np.abs(g.taxels - ref).max() < 1e-6


def test_surface_values_and_cop():
    g = load_geometry("S1813E")
    v = np.zeros(31)
    v[15] = 10
    sv = g.surface_values(v)
    assert sv.shape == (660,) and sv.max() > 0 and np.all(sv[(g.neighbor != 15).all(axis=1)] == 0)
    assert np.allclose(g.cop(v), g.taxels[15])
    assert np.isnan(g.cop(np.zeros(31))).all()


def test_window_slopes():
    t = np.arange(0, 2, 0.1)
    v = 3 * t + 1
    s = window_slopes(t, v, np.array([0.5, 1.0, 5.0]), 0.2, 3)
    assert s[:2] == pytest.approx([3, 3]) and np.isnan(s[2])


def test_pairing_interpolates_and_drops_gaps():
    st = np.array([0.0, 0.01, 0.02, 0.5, 0.51])
    fr = np.array([[0, 0, 10], [0, 0, 20], [0, 0, 30], [0, 0, 40], [0, 0, 50]])
    s = SensorSeries(st, fr, np.zeros((5, 31)))
    c = pair_sensor(np.array([0.015, 0.2, 0.505]), np.array([2.0, 2.0, 4.5]), s, bench_settings())
    assert c["F_N"][0] == pytest.approx(2.5) and np.isnan(c["F_N"][1]) and c["F_N"][2] == pytest.approx(4.5)
    assert not c["contact"][1] and c["error_N"][0] == pytest.approx(0.5)


# ── synthetic session analysis ──
@pytest.fixture(scope="module")
def usb_session(tmp_path_factory):
    d = tmp_path_factory.mktemp("bench") / "2026-10-05-100000_S1813E_A1"
    make_session(d, [SynthSensor("A1")], seconds=90, gauge_outliers=2)
    return d, analyze_session(d)


def test_synthetic_known_errors(usb_session):
    _, res = usb_session
    o = res.metrics.iloc[0]
    assert o["scope"] == "overall" and len(res.metrics) == 1   # no test points selected → overall only
    assert o["slope"] == pytest.approx(1.05, abs=0.01) and o["intercept"] == pytest.approx(0.2, abs=0.06)
    # through-origin gain absorbs the +0.2 N offset: 1.05 + 0.2·Σg/Σg² over the 4/8/12 N holds
    assert o["gain"] == pytest.approx(1.05 + 0.2 * 24 / 224, abs=0.01) and o["resid_sd_N"] < 0.15
    assert o["noload_mean_N"] == pytest.approx(0.1, abs=0.01)
    s = res.sensors[0]
    assert s["model"] == "S1813E" and s["lag_s"] == pytest.approx(0.08, abs=0.011)
    assert any("2 gauge outliers" in w for w in res.warnings) and any("No test points" in w for w in res.warnings)
    assert res.points == [] and res.result["error_map"] is None and (res.samples["point"] == "").all()


def test_lag_correction(usb_session):
    """Sensor lag 80 ms → the analysis measures the session lag, shifts gauge times, and the error during force changes shrinks."""
    d, res = usb_session
    assert res.sensors[0]["lag_applied_s"] == pytest.approx(0.08, abs=0.011)
    off = analyze_session(d, {"lag_correct": False}, write=False)
    assert off.sensors[0]["lag_applied_s"] == 0 and off.sensors[0]["lag_s"] == res.sensors[0]["lag_s"]
    on_c, off_c = res.metrics.iloc[0]["rmse_contact_N"], off.metrics.iloc[0]["rmse_contact_N"]
    assert on_c < off_c * 0.8
    low = analyze_session(d, {"lag_min_r": 1.01}, write=False)   # no correction when the correlation is below the threshold
    assert low.sensors[0]["lag_applied_s"] == 0


def test_stable_filter_excludes_ramps(usb_session):
    _, res = usb_session
    st = res.samples[res.samples["stable"] == 1]
    lv = np.array([4.0, 8.0, 12.0])
    assert len(st) > 200
    assert np.min(np.abs(st["gauge_N"].to_numpy()[:, None] - lv), axis=1).max() < 0.05   # hold segments only
    ct = res.samples[(res.samples["contact"] == 1) & (res.samples["stable"] == 0)]
    assert len(ct) > 100   # ramp up/down samples are contact but not stable


def test_result_files_and_reanalysis_deterministic(usb_session, tmp_path):
    d, res = usb_session
    for name in ("samples.csv", "metrics.csv", "result.json", "report.html", "plots/overall_error.png"):
        assert (d / name).is_file(), name
    assert sorted(p.name for p in (d / "plots").iterdir()) == ["noload.png", "overall_error.png"]
    before = {n: (d / n).read_bytes() for n in ("samples.csv", "metrics.csv")}
    r1 = json.loads((d / "result.json").read_text(encoding="utf-8"))
    analyze_session(d)
    r2 = json.loads((d / "result.json").read_text(encoding="utf-8"))
    for n, b in before.items():
        assert (d / n).read_bytes() == b, n
    r1.pop("analyzed_at"), r2.pop("analyzed_at")
    assert r1 == r2
    back = load_result(d)
    assert len(back.samples) == len(res.samples) and back.sensors[0]["label"] == "A1"
    assert b"\r\n" not in before["samples.csv"]


def test_override_settings_recorded(usb_session, tmp_path):
    d, _ = usb_session
    res = analyze_session(d, {"stable_slope_N_per_s": 1.0}, write=False)
    assert res.settings["stable_slope_N_per_s"] == 1.0


def test_session_csv_is_pxsr_format(usb_session):
    d, res = usb_session
    log = read_log(d / res.result["sensor_csv"])
    frame = Frame(0.0, 0, 0, "S1813E", (0, 0, 0), (0,) * 93)
    assert log.header == pxsr_csv.build_header([[frame]])


def test_hand_crosstalk_simultaneous_and_channel_check(tmp_path):
    sensors = [SynthSensor("A1", "S1813E", channel=0), SynthSensor("A2", "S1813E", channel=1),
               SynthSensor("B1", "S2015", channel=2), SynthSensor("B2", "S2015", channel=3)]
    actual = [0, 1, 3, 2]   # B2 pressed on B1's turn, B1 on B2's turn (labels swapped)
    seg = 10                # next sensor every 10 presses (30 s)
    events = [{"kind": "sensor_switch", "label": lab, "t_rel": i * seg * 3.0} for i, lab in enumerate(["A1", "A2", "B1", "B2"])]
    d = make_session(tmp_path / "hand", sensors, seconds=120, schedule=lambda k: actual[(k // seg) % 4],
                     crosstalk=0.05, simultaneous_every=7, events=events)
    res = analyze_session(d)
    assert [s["label"] for s in res.sensors] == ["A1", "A2", "B1", "B2"]
    ct = res.crosstalk
    # 5 % interference (rounding to raw 0.1 N units makes the ratio slightly larger at low force)
    assert len(ct) == 12 and ct["p95_ratio_pct"].between(3.5, 7.5).all()
    assert res.result["counts"]["simultaneous"] > 0
    assert res.samples.loc[res.samples["simultaneous"] == 1, "stable"].eq(0).all()
    cc = res.result["channel_check"]
    assert [c["match"] for c in cc] == [True, True, False, False]
    assert any("Channel mapping" in w for w in res.warnings)
    srows = res.metrics[res.metrics["scope"] == "sensor"]
    assert list(srows["name"]) == ["A1", "A2", "B1", "B2"] and (srows["slope"] - 1.05).abs().max() < 0.02
    for name in ("crosstalk.csv", "plots/sensors_compare.png", "plots/crosstalk.png", "plots/B2/overall_error.png"):
        assert (d / name).is_file(), name


# ── coverage (during recording) = same rules as the analysis ──
def test_coverage_matches_analysis(tmp_path):
    d = make_session(tmp_path / "cov", [SynthSensor("A1")], seconds=40)
    res = analyze_session(d, write=False)
    log = read_log(d / res.result["sensor_csv"])
    key = next(iter(log.sensors))
    fr = log.force_raw(key)
    cov = Coverage(dict(res.settings, lag_correct=False))
    gt = np.loadtxt(d / "gauge.csv", delimiter=",", skiprows=1)
    gi = 0
    for i, t in enumerate(log.t):
        cov.add_frame(Frame(t, 0, 0, "S1813E", tuple(int(x) for x in fr[i]), (0,) * 93))
        while gi < len(gt) and gt[gi, 0] <= t:
            cov.add_gauge(gt[gi, 0], gt[gi, 1])
            gi += 1
        if i % 250 == 0:
            cov.update()
    cov.update()
    # the tail (last window) may not be processed yet; the analysis also corrects the 80 ms gauge lag
    ref = analyze_session(d, {"lag_correct": False}, write=False)
    assert abs(cov.total_stable - ref.result["counts"]["stable"]) <= 10 and cov.total_stable > 50
    assert abs(cov.total_contact - ref.result["counts"]["contact"]) <= 10
    assert cov.no_point == cov.total_stable   # no test point selected


def test_noload_check_warns():
    sb, gb = TimeSeriesBuffer(ncols=3), TimeSeriesBuffer(ncols=1)
    for i in range(30):
        sb.append(i * 0.1, (0, 0, 5))
        gb.append(i * 0.1, 0.05)
    r = noload_check(sb, gb, 0.0, 3.0, 0.3)
    assert r["gauge_mean_N"] == pytest.approx(0.05) and r["sensor_F_mean_N"] == pytest.approx(0.5)
    assert len(r["warnings"]) == 1 and "Sensor" in r["warnings"][0]


def test_end_residual():
    from paxkit.bench.analyze import end_residual
    s = bench_settings()
    t = np.arange(0.0, 10.0, 0.1)
    off = t >= 7.95                                  # hands off for the last 2 s; the gauge keeps a 0.2 N offset
    cols = {"t_unix_s": t, "paired": np.ones(len(t), bool), "F_N": np.where(off, 0.6, 5.0),
            "Fx_N": np.where(off, 0.5, 3.0), "Fy_N": np.where(off, 0.0, 0.0), "Fz_N": np.where(off, 0.3, 4.0),
            "gauge_N": np.where(off, 0.2, 5.0), "slope_gauge": np.zeros(len(t)), "slope_sensor": np.zeros(len(t))}
    cols["slope_gauge"][-1] = np.nan                 # last sample: too few points in the slope window
    r = end_residual(cols, s)
    assert r["seconds"] == pytest.approx(1.9) and r["sensor_F_mean_N"] == pytest.approx(0.6)
    assert r["gauge_mean_N"] == pytest.approx(0.2) and r["sensor_mean_N"] == pytest.approx([0.5, 0.0, 0.3])
    assert end_residual(cols, {**s, "end_residual_min_s": 3.0}) is None        # too short
    assert end_residual({**cols, "gauge_N": np.full(len(t), 5.0)}, s) is None   # still pressing at the end


# ── session recording (simulated sensor + gauge, real time) ──
def test_bench_session_with_sim(tmp_path):
    tr = SimUsbTransport("S1813E", period=1.0)
    s = UsbSensor(tr)
    g = SimGauge(tr.load_N, noise_N=0.0, rate_hz=10, clock=s.clock)
    s.start()
    g.start()
    time.sleep(1.0)
    sess = BenchSession(s, g, bench_settings(), label="A 1/x", model="S1813E", sensor_info={"mode": "usb"},
                        gauge_info={"port": "sim"}, root=tmp_path)
    sess.note_event("noload_check", gauge_mean_N=0.0)
    folder = sess.start()
    time.sleep(3.0)
    sess.stop()
    g.stop()
    s.disconnect()
    g.join(1)
    s.join(3)
    assert folder.name.endswith("_S1813E_A_1_x") and sess.status == "stopped"
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["sensors"][0]["sensor"] == "S1813E" and meta["settings"]["max_N"] == 15
    assert [e["kind"] for e in meta["events"]] == ["noload_check"] and meta["gauge"]["rows"] == sess.gauge_rows
    lines = (folder / "gauge.csv").read_bytes().split(b"\n")
    assert lines[0] == b"t_unix_s,force_N" and 25 <= sess.gauge_rows <= 35 and len(lines) == sess.gauge_rows + 2
    assert sess.sensor_csv is not None and (folder / (sess.sensor_csv.stem + ".json")).is_file()
    res = analyze_session(folder)
    assert res.result["counts"]["paired"] >= 25 and res.sensors[0]["frames"] == sess.sensor_rows
