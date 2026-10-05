"""게이지 테스트 (계획 P6-6 검증): 점 모델·구역, 합성 세션 분석, 재분석 결정성, 커버리지, 세션 기록."""
import json
import time
from pathlib import Path

import numpy as np
import pytest

from bench_synth import START, SynthSensor, make_session
from paxkit.bench import BenchSession, Coverage, analyze_session, bench_settings, load_result, load_zones, noload_check
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


# ── 점 모델·구역 ──
@pytest.mark.parametrize("label,old,n,m", [("S1813E", "S1813E", 31, 660), ("S2015", "S2015E", 52, 1156)])
def test_geometry_matches_paxtest(label, old, n, m):
    g = load_geometry(label)
    assert g.taxels.shape == (n, 3) and g.surface.shape == (m, 3) and g.neighbor.max() < n
    assert load_geometry(old) is g   # config의 S2015E 이름도 같은 모델
    p = PAXTEST_GEOM / f"{old}.json"
    if not p.is_file():
        pytest.skip("paxtest geometry 없음")
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


@pytest.mark.parametrize("label", ["S1813E", "S2015"])
def test_zones_cover_every_taxel_once(label):
    zs = load_zones(label)
    allt = sorted(i for z in zs.zones for i in z.taxels)
    assert allt == list(range(zs.geometry.n_taxels)) and len(zs.zones) == 7
    assert {(z.row, z.col) for z in zs.zones} == {(0, 1)} | {(r, c) for r in (1, 2) for c in range(3)}
    tz = np.zeros((1, zs.geometry.n_taxels))
    tz[0, list(zs.zones[4].taxels)] = 20
    assert zs.classify(tz, 5)[0] == 4 and zs.classify(tz * 0, 5)[0] == -1


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


# ── 합성 세션 분석 ──
@pytest.fixture(scope="module")
def usb_session(tmp_path_factory):
    d = tmp_path_factory.mktemp("bench") / "2026-10-05-100000_S1813E_A1"
    make_session(d, [SynthSensor("A1", zone_bias={"tip": -0.5})], seconds=90, gauge_outliers=2)
    return d, analyze_session(d)


def test_synthetic_known_errors(usb_session):
    _, res = usb_session
    m = res.metrics.set_index("id")
    for zid, row in m[res.metrics.set_index("id")["scope"] == "zone"].iterrows():
        if row["slope"] is None or np.isnan(row["slope"]):
            continue
        assert row["slope"] == pytest.approx(1.05, abs=0.01), zid
        want = -0.3 if zid == "tip" else 0.2
        assert row["intercept"] == pytest.approx(want, abs=0.06), zid
    o = res.metrics.iloc[0]
    assert o["scope"] == "overall" and o["noload_mean_N"] == pytest.approx(0.1, abs=0.01)
    s = res.sensors[0]
    assert s["model"] == "S1813E" and s["lag_s"] == pytest.approx(0.08, abs=0.011)
    assert any("이상값 2개" in w for w in res.warnings)
    assert sum(bool(m.loc[z, "enough"]) for z in m.index if m.loc[z, "scope"] == "zone") >= 5


def test_lag_correction(usb_session):
    """센서 지연 80 ms → 분석이 세션 지연을 재서 게이지 시각을 옮기고, 힘이 변하는 구간 오차가 줄어든다."""
    d, res = usb_session
    assert res.sensors[0]["lag_applied_s"] == pytest.approx(0.08, abs=0.011)
    off = analyze_session(d, {"lag_correct": False}, write=False)
    assert off.sensors[0]["lag_applied_s"] == 0 and off.sensors[0]["lag_s"] == res.sensors[0]["lag_s"]
    on_c, off_c = res.metrics.iloc[0]["rmse_contact_N"], off.metrics.iloc[0]["rmse_contact_N"]
    assert on_c < off_c * 0.8
    low = analyze_session(d, {"lag_min_r": 1.01}, write=False)   # 상관이 기준보다 낮으면 보정 안 함
    assert low.sensors[0]["lag_applied_s"] == 0


def test_stable_filter_excludes_ramps(usb_session):
    _, res = usb_session
    st = res.samples[res.samples["stable"] == 1]
    lv = np.array([4.0, 8.0, 12.0])
    assert len(st) > 200
    assert np.min(np.abs(st["gauge_N"].to_numpy()[:, None] - lv), axis=1).max() < 0.05   # 유지 구간만
    ct = res.samples[(res.samples["contact"] == 1) & (res.samples["stable"] == 0)]
    assert len(ct) > 100   # 올림·내림 샘플은 접촉이지만 안정 아님


def test_result_files_and_reanalysis_deterministic(usb_session, tmp_path):
    d, res = usb_session
    for name in ("samples.csv", "metrics.csv", "result.json", "report.html", "plots/overall_error.png",
                 "plots/zone_map.png", "plots/zones.png"):
        assert (d / name).is_file(), name
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
    actual = [0, 1, 3, 2]   # B1 차례에 B2를, B2 차례에 B1을 누름 (라벨 뒤바뀜)
    seg = 10                # 누름 10번(30 s)마다 다음 센서
    events = [{"kind": "sensor_switch", "label": lab, "t_rel": i * seg * 3.0} for i, lab in enumerate(["A1", "A2", "B1", "B2"])]
    d = make_session(tmp_path / "hand", sensors, seconds=120, schedule=lambda k: actual[(k // seg) % 4],
                     crosstalk=0.05, simultaneous_every=7, events=events)
    res = analyze_session(d)
    assert [s["label"] for s in res.sensors] == ["A1", "A2", "B1", "B2"]
    ct = res.crosstalk
    # 간섭 5 % (raw 0.1 N 단위로 반올림돼 약한 힘에서 비율이 조금 커진다)
    assert len(ct) == 12 and ct["p95_ratio_pct"].between(3.5, 7.5).all()
    assert res.result["counts"]["simultaneous"] > 0
    assert res.samples.loc[res.samples["simultaneous"] == 1, "stable"].eq(0).all()
    cc = res.result["channel_check"]
    assert [c["match"] for c in cc] == [True, True, False, False]
    assert any("채널 대응" in w for w in res.warnings)
    srows = res.metrics[res.metrics["scope"] == "sensor"]
    assert list(srows["name"]) == ["A1", "A2", "B1", "B2"] and (srows["slope"] - 1.05).abs().max() < 0.02
    for name in ("crosstalk.csv", "plots/sensors_compare.png", "plots/crosstalk.png", "plots/B2/zones.png"):
        assert (d / name).is_file(), name


# ── 커버리지 (기록 중) = 분석과 같은 규칙 ──
def test_coverage_matches_analysis(tmp_path):
    d = make_session(tmp_path / "cov", [SynthSensor("A1")], seconds=40)
    res = analyze_session(d, write=False)
    log = read_log(d / res.result["sensor_csv"])
    key = next(iter(log.sensors))
    fr, tz = log.force_raw(key), log.taxel_raw(key)
    zs = load_zones("S1813E")
    cov = Coverage(zs, res.settings)
    gt = np.loadtxt(d / "gauge.csv", delimiter=",", skiprows=1)
    gi = 0
    for i, t in enumerate(log.t):
        grid = []
        for v in tz[i]:
            grid += [0, 0, int(v)]
        cov.add_frame(Frame(t, 0, 0, "S1813E", tuple(int(x) for x in fr[i]), tuple(grid)))
        while gi < len(gt) and gt[gi, 0] <= t:
            cov.add_gauge(gt[gi, 0], gt[gi, 1])
            gi += 1
        if i % 250 == 0:
            cov.update()
    cov.update()
    per_zone = res.samples[res.samples["stable"] == 1].groupby("zone").size()
    ids = [z.id for z in zs.zones]
    got = dict(zip(ids, cov.stable))
    # 끝 부분(마지막 창)은 아직 처리 전일 수 있다
    assert sum(got.values()) >= res.result["counts"]["stable"] - 10
    for zid, n in per_zone.items():
        assert abs(got[zid] - n) <= 10
    assert cov.enough().sum() >= 1 and cov.total_stable > 50


def test_noload_check_warns():
    sb, gb = TimeSeriesBuffer(ncols=3), TimeSeriesBuffer(ncols=1)
    for i in range(30):
        sb.append(i * 0.1, (0, 0, 5))
        gb.append(i * 0.1, 0.05)
    r = noload_check(sb, gb, 0.0, 3.0, 0.3)
    assert r["gauge_mean_N"] == pytest.approx(0.05) and r["sensor_F_mean_N"] == pytest.approx(0.5)
    assert len(r["warnings"]) == 1 and "센서" in r["warnings"][0]


# ── 세션 기록 (시뮬레이션 센서 + 게이지, 실제 시간) ──
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
