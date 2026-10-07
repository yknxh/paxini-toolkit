"""Test points (location by click, 2026-10-06): point picking, sample assignment, error %, click-mode analysis and error map."""
import json

import numpy as np
import pytest

from bench_synth import PERIOD, SynthSensor, make_session
from paxkit.bench import Coverage, analyze_session, load_result
from paxkit.bench.metrics import gain_fit
from paxkit.bench.points import (PointSet, assign, error_pct, load_zones, points_from_events, select_event, selections,
                                 usable_summary, zone_of)
from paxkit.device.frames import Frame
from paxkit.device.geometry import load_geometry
from paxkit.recording.reader import read_log


def test_pick_snaps_merges_and_rejects_outside():
    g = load_geometry("S1813E")
    ps = PointSet(g)
    top = int(np.argmax(g.taxels[:, 2]))
    p1 = ps.pick(*g.taxels[top, :2])
    assert p1.id == "P1" and p1.z_mm > 4.0
    assert ps.pick(p1.x_mm + 0.5, p1.y_mm) is p1          # within MERGE_MM → same point
    p2 = ps.pick(g.taxels[0, 0], g.taxels[0, 1])
    assert p2.id == "P2" and ps.get("P2") is p2
    assert ps.pick(50.0, 50.0) is None and len(ps.points) == 2
    assert 0 <= p1.taxel < g.n_taxels


@pytest.mark.parametrize("model", ["S1813E", "S2015"])
def test_zones_cover_every_taxel_once(model):
    """points.png groups test points by zone: each taxel (and so each point) is in exactly one zone."""
    g = load_geometry(model)
    zones = load_zones(model)
    assert len(zones) == 7 and len({(z.row, z.col) for z in zones}) == 7
    taxels = sorted(i for z in zones for i in z.taxels)
    assert taxels == list(range(g.n_taxels))
    ps = PointSet(g)
    top = int(np.argmax(g.taxels[:, 2]))
    p = ps.pick(*g.taxels[top, :2])
    assert zone_of(zones, p) is not None and p.taxel in zone_of(zones, p).taxels


def test_selections_and_assign():
    ev = [{"kind": "point_select", "t_unix_s": 10.0, "point": "P1", "x_mm": 1, "y_mm": 2, "z_mm": 3, "taxel": 4},
          {"kind": "noload_check", "t_unix_s": 11.0},
          {"kind": "point_select", "t_unix_s": 20.0, "point": None},
          {"kind": "point_select", "t_unix_s": 30.0, "point": "P2", "x_mm": 5, "y_mm": 6, "z_mm": 7, "taxel": 8},
          {"kind": "point_select", "t_unix_s": 40.0, "point": "P1", "x_mm": 1, "y_mm": 2, "z_mm": 3, "taxel": 4}]
    t, ids = selections(ev)
    assert list(ids) == ["P1", "", "P2", "P1"]
    got = assign(np.array([5.0, 10.0, 15.0, 25.0, 35.0, 45.0]), t, ids)
    assert list(got) == ["", "P1", "P1", "", "P2", "P1"]
    assert [p.id for p in points_from_events(ev)] == ["P1", "P2"]
    assert select_event(None) == {"point": None}


def test_error_pct_is_mean_of_per_sample_ratios():
    bias, absd = error_pct(np.array([1.0, 10.0]), np.array([0.5, -0.5]))
    assert bias == pytest.approx((50.0 - 5.0) / 2) and absd == pytest.approx((50.0 + 5.0) / 2)
    assert error_pct(np.array([]), np.array([])) == (None, None)


def test_gain_fit():
    g = np.array([2.0, 4.0, 8.0, 12.0])
    a, sd = gain_fit(g, 0.8 * g)
    assert a == pytest.approx(0.8) and sd == pytest.approx(0.0, abs=1e-12)
    assert gain_fit(g[:2], g[:2]) == (None, None)


def test_usable_summary():
    x, y = np.array([0.0, 10.0, 0.0, 10.0]), np.array([0.0, 0.0, 10.0, 10.0])
    ae = np.array([0.0, 0.0, 20.0, 20.0])
    r = usable_summary(x, y, ae, 10.0)
    assert r["points_ok"] == 2 and r["area_mm2"] == pytest.approx(100.0, rel=0.05)
    assert r["area_ok_pct"] == pytest.approx(50.0, abs=3.0)
    assert usable_summary(x, y, ae, 30.0)["points_ok"] == 4
    assert usable_summary(x[:2], y[:2], ae[:2], 10.0)["area_mm2"] is None   # < 3 points: no area


@pytest.fixture(scope="module")
def click_session(tmp_path_factory):
    """Synthetic session where the tester selects the pressed spot before each press (press k = taxel (7k) % N).
    Taxels at the tip (y ≥ 14 mm) read 2 N high."""
    g = load_geometry("S1813E")
    tip = {i: 2.0 for i in range(g.n_taxels) if g.taxels[i, 1] >= 14.0}
    ps = PointSet(g)
    events, pressed = [], {}
    for k in range(40):
        c = (k * 7) % g.n_taxels
        p = ps.pick(*g.taxels[c, :2])
        pressed[p.id] = c
        events.append(dict(kind="point_select", t_rel=k * PERIOD - 0.05, **select_event(p)))
    d = tmp_path_factory.mktemp("bench") / "2026-10-06-100000_S1813E_A1"
    make_session(d, [SynthSensor("A1", taxel_bias=tip)], seconds=120, events=events)
    return d, analyze_session(d), pressed, tip


def test_click_mode_locates_by_point(click_session):
    d, res, pressed, tip_taxels = click_session
    r = res.result
    assert len(res.points) == len(pressed) == 31
    pts = res.metrics[res.metrics["scope"] == "point"].set_index("id")
    assert list(pts.index) == [p.id for p in res.points]
    # error % uses stable samples only: one-press points (~9 stable samples) fall below point_min_samples (10)
    assert pts["enough"].sum() >= 15 and (pts["enough"] == (pts["n_fit"] >= 10)).all()
    assert set(res.samples["point"]) - {""} == set(pressed) and "zone" not in res.samples.columns
    # mean |e| % follows the synthetic errors: tip points (+2 N) far above the others (1.05·L + 0.2 N → 5 % + 0.2 N / gauge)
    tip = [pid for pid, c in pressed.items() if c in tip_taxels]
    ok = pts[pts["enough"]]
    tip_ok = [i for i in tip if i in ok.index]
    assert tip_ok and ok.loc[tip_ok, "abs_err_pct"].min() > 20
    rest = ok.drop(index=tip_ok)
    assert rest["abs_err_pct"].between(5, 12).all() and (rest["bias_pct"] > 0).all()
    em = r["error_map"]
    assert [e["k_pct"] for e in em] == [5, 10, 20, 30] and em[-1]["points"] == ok.shape[0]
    assert all(a["points_ok"] <= b["points_ok"] for a, b in zip(em, em[1:]))
    assert em[0]["points_ok"] == 0 and em[2]["points_ok"] == len(rest)
    plots = sorted(p.name for p in (d / "plots").iterdir())
    assert plots == ["error_map.png", "noload.png", "overall_error.png", "points.png"]
    assert "Test points selected" in (d / "report.html").read_text("utf-8")
    back = load_result(d)
    assert [p.id for p in back.points] == [p.id for p in res.points] and (back.samples["point"] != "").any()


def test_taxel_mode_without_selections(tmp_path):
    d = make_session(tmp_path / "s", [SynthSensor("A1")], seconds=20)
    res = analyze_session(d, write=False)
    assert res.points == [] and res.result["error_map"] is None
    assert (res.samples["point"] == "").all() and not (res.metrics["scope"] == "point").any()


def test_coverage_point_tallies_match_analysis(click_session):
    d, res, _, _ = click_session
    log = read_log(d / res.result["sensor_csv"])
    key = next(iter(log.sensors))
    fr = log.force_raw(key)
    cov = Coverage(res.settings)
    ev = json.loads((d / "meta.json").read_text("utf-8"))["events"]
    sel = sorted((e for e in ev if e["kind"] == "point_select"), key=lambda e: e["t_unix_s"])
    gt = np.loadtxt(d / "gauge.csv", delimiter=",", skiprows=1)
    gi = si = 0
    for i, t in enumerate(log.t):
        while si < len(sel) and sel[si]["t_unix_s"] <= t:
            cov.select_point(sel[si]["t_unix_s"], sel[si]["point"])
            si += 1
        cov.add_frame(Frame(t, 0, 0, "S1813E", tuple(int(x) for x in fr[i]), (0,) * 93))
        while gi < len(gt) and gt[gi, 0] <= t:
            cov.add_gauge(gt[gi, 0], gt[gi, 1])
            gi += 1
        if i % 250 == 0:
            cov.update()
    cov.update()
    # live coverage does not correct the gauge lag → compare with an analysis without lag correction
    ref = analyze_session(d, {"lag_correct": False}, write=False)
    pts = ref.metrics[ref.metrics["scope"] == "point"].set_index("id")
    for pid in pts.index:
        n, nfit, pct = cov.point_stats(pid)
        assert abs(n - pts.loc[pid, "n_contact"]) <= 3 and abs(nfit - pts.loc[pid, "n_fit"]) <= 3, pid
        if pts.loc[pid, "enough"]:
            assert pct == pytest.approx(pts.loc[pid, "abs_err_pct"], abs=1.0), pid
