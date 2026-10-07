"""Result figures (plan P6-3): build matplotlib `Figure`s used for both files (PNG) and the GUI.

pyplot is not used (`Figure` is created directly so it does not depend on the GUI thread or backend).
theme: "light" = files and report, "dark" = GUI (same content, only colors differ).

Sensor drawings are a top view (x–y projection, right = +x, up = +y = rounded tip).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from ..device.geometry import Geometry
from .metrics import binned
from .points import error_pct, load_zones, triangulation, zone_of

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#222222", "muted": "#888888", "grid": "#dddddd", "surface": "#c8c8c8",
              "point": "#1f77b4", "band": "#ff7f0e", "zero": "#444444", "thin": "#bbbbbb", "cmap": "viridis",
              "other": "#aaaaaa", "good": "#2ca02c", "bad": "#d62728"},
    "dark": {"bg": "#1e1f22", "fg": "#dcdcdc", "muted": "#8a8a8a", "grid": "#3a3b3f", "surface": "#4a4b50",
             "point": "#4fa3e0", "band": "#ffa040", "zero": "#bbbbbb", "thin": "#555555", "cmap": "viridis",
             "other": "#6e6e6e", "good": "#4cc35a", "bad": "#ef5350"},
}
PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#9a6324"]


def lag_text(sensor: Dict) -> str:
    """Lag shown for a sensor: measured value, correlation, and whether the analysis corrected it."""
    lag = sensor.get("lag_s")
    if lag is None:
        return "- (too little force change to measure)"
    applied = sensor.get("lag_applied_s") or 0.0
    return f"{lag * 1e3:+.0f} ms (r {sensor.get('lag_r'):.2f}, " + ("corrected)" if applied else "not corrected)")


def _fig(w: float, h: float, theme: str) -> Figure:
    t = THEMES[theme]
    f = Figure(figsize=(w, h), facecolor=t["bg"], layout="constrained")
    return f


def _style(ax, theme: str) -> None:
    t = THEMES[theme]
    ax.set_facecolor(t["bg"])
    for sp in ax.spines.values():
        sp.set_color(t["muted"])
    ax.tick_params(colors=t["fg"], labelsize=8)
    ax.xaxis.label.set_color(t["fg"])
    ax.yaxis.label.set_color(t["fg"])
    ax.title.set_color(t["fg"])
    ax.grid(True, color=t["grid"], linewidth=0.6)


def _fmt(v, unit: str = "", nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    return f"{v:.{nd}f}{unit}"


def error_limits(samples: pd.DataFrame, settings: Dict) -> tuple:
    """Axis limits shared by all error plots (from contact samples, since every contact sample is plotted)."""
    st = samples[samples["contact"] == 1]
    xmax = max(float(settings.get("max_N", 15)), float(st["gauge_N"].max()) if len(st) else 0.0) * 1.05
    e = st["error_N"].to_numpy(dtype=float)
    lim = max(0.5, float(np.percentile(np.abs(e), 99.5)) * 1.15) if len(e) else 1.0
    return (0.0, xmax), (-lim, lim)


def draw_error(ax, samples: pd.DataFrame, settings: Dict, theme: str, xlim=None, ylim=None,
               title: Optional[str] = None, small: bool = False) -> None:
    """Error plot: x = gauge N, y = |F| − gauge N. Zero line + every contact sample (not stable = gray, stable = blue)
    + binned mean ±1 SD band of stable samples (orange) + binned mean of all contact samples (dashed)
    (user decision 2026-10-06)."""
    t = THEMES[theme]
    _style(ax, theme)
    ct = samples[samples["contact"] == 1]
    on = ct["stable"].to_numpy() == 1
    g = ct["gauge_N"].to_numpy(dtype=float)
    e = ct["error_N"].to_numpy(dtype=float)
    size = 4 if small else 6
    ax.axhline(0, color=t["zero"], linewidth=0.8)
    ax.scatter(g[~on], e[~on], s=size, color=t["other"], alpha=0.3, linewidths=0, rasterized=True,
               label="Not stable")
    ax.scatter(g[on], e[on], s=size, color=t["point"], alpha=0.4 if on.sum() > 300 else 0.6, linewidths=0,
               rasterized=True, label="Stable")
    bin_N = float(settings.get("bin_N", 1.0))
    c, m, sd, _ = binned(g[on], e[on], bin_N)
    if len(c):
        ax.fill_between(c, m - sd, m + sd, color=t["band"], alpha=0.25, linewidth=0)
        ax.plot(c, m, color=t["band"], linewidth=1.6 if not small else 1.2, marker="o", markersize=2.5,
                label="Stable mean ±1 SD")
    c, m, _, _ = binned(g, e, bin_N)
    if len(c):
        ax.plot(c, m, color=t["fg"], linewidth=1.2 if not small else 0.9, linestyle="--", alpha=0.8,
                label="All contact mean")
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)
    if not small:
        ax.set_xlabel("Gauge (N)")
        ax.set_ylabel("Error |F| − gauge (N)")
        leg = ax.legend(loc="best", fontsize=8, frameon=True, markerscale=2.5, facecolor=t["bg"], edgecolor=t["grid"],
                        labelcolor=t["fg"])
        for h in leg.legend_handles:
            h.set_alpha(1.0)
    if title:
        ax.set_title(title, fontsize=9 if small else 11)


def _metrics_text(row: Dict, result: Dict, sensor: Optional[Dict]) -> str:
    lines = [
        f"Stable samples n = {row.get('n', 0)}",
        f"Gauge range {_fmt(row.get('gauge_min_N'), '', 1)} – {_fmt(row.get('gauge_max_N'), ' N', 1)}",
        f"bias {_fmt(row.get('bias_N'), ' N')}",
        f"SD {_fmt(row.get('sd_N'), ' N')}",
        f"RMSE {_fmt(row.get('rmse_N'), ' N')}  ({_fmt(row.get('rmse_pct_fs'), ' %FS', 2)})",
        f"MAE {_fmt(row.get('mae_N'), ' N')}",
        f"P95 |e| {_fmt(row.get('p95_abs_N'), ' N')}",
        f"Max |e| {_fmt(row.get('max_abs_N'), ' N')}",
        f"|F| = {_fmt(row.get('slope'), '', 4)}·gauge {'+' if (row.get('intercept') or 0) >= 0 else '−'} "
        f"{_fmt(abs(row.get('intercept') or 0), ' N')}",
        f"R² {_fmt(row.get('r2'), '', 5)}",
        f"Z only: bias {_fmt(row.get('bias_z_N'), ' N')}, RMSE {_fmt(row.get('rmse_z_N'), ' N')}",
        "",
        f"Mean |e|/gauge {_fmt(row.get('abs_err_pct'), ' %', 1)} (n = {row.get('n_fit') or 0}), "
        f"signed {_fmt(row.get('bias_pct'), ' %', 1)}",
        f"Reference: gain {_fmt(row.get('gain'), '', 3)} (|F| = gain·gauge), scatter {_fmt(row.get('resid_sd_N'), ' N', 2)}",
        "",
        f"All contact samples n = {row.get('n_contact', 0)}",
        f"bias {_fmt(row.get('bias_contact_N'), ' N')}",
        f"SD {_fmt(row.get('sd_contact_N'), ' N')}",
        f"RMSE {_fmt(row.get('rmse_contact_N'), ' N')}",
        "",
    ]
    if row.get("n_noload") is not None:
        lines.append(f"No-load residual |F| mean {_fmt(row.get('noload_mean_N'), ' N')}, "
                     f"max {_fmt(row.get('noload_max_N'), ' N')}")
    if sensor is not None:
        lines.append("Residual lag (sensor − gauge) " + lag_text(sensor))
    return "\n".join(lines)


def overall_error_figure(samples: pd.DataFrame, row: Dict, result: Dict, sensor: Optional[Dict] = None,
                         theme: str = "light", title: str = "Overall error") -> Figure:
    t = THEMES[theme]
    f = _fig(10, 5.2, theme)
    gs = f.add_gridspec(1, 2, width_ratios=[2.3, 1])
    ax = f.add_subplot(gs[0, 0])
    xlim, ylim = error_limits(samples, result["settings"])
    draw_error(ax, samples, result["settings"], theme, xlim, ylim, title=title)
    tx = f.add_subplot(gs[0, 1])
    tx.axis("off")
    tx.set_facecolor(t["bg"])
    tx.text(0.0, 1.0, _metrics_text(row, result, sensor), va="top", ha="left", fontsize=9, color=t["fg"],
            transform=tx.transAxes, linespacing=1.5)
    return f


def _plain_sensor(ax, g: Geometry, theme: str) -> None:
    t = THEMES[theme]
    ax.set_facecolor(t["bg"])
    ax.scatter(g.surface[:, 0], g.surface[:, 1], s=3, color=t["surface"], linewidths=0, zorder=0)
    ax.scatter(g.taxels[:, 0], g.taxels[:, 1], s=12, color=t["thin"], linewidths=0, zorder=0)
    pad = 1.0
    lo, hi = g.surface.min(axis=0), g.surface.max(axis=0)
    ax.set_xlim(lo[0] - pad, hi[0] + pad)
    ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)


def _ext_levels(ks: List[float], vmax: float) -> List[float]:
    """k values, continued past vmax in steps of the last gap."""
    lv = sorted(float(k) for k in ks if k > 0) or [10.0]
    step = (lv[-1] - lv[-2]) if len(lv) > 1 else lv[0]
    while lv[-1] < vmax:
        lv.append(lv[-1] + step)
    return lv


def _point_rows(points, point_rows: pd.DataFrame):
    """id → metrics row, and the points used in the maps (enough samples, values present)."""
    by_id = {r["id"]: r for r in point_rows.to_dict(orient="records")}
    ok = lambda v: v is not None and np.isfinite(v)
    use = [p for p in points if by_id.get(p.id, {}).get("enough") and ok(by_id[p.id].get("abs_err_pct"))]
    return by_id, use


def error_map_figure(g: Geometry, points, point_rows: pd.DataFrame, settings: Dict, summary: Optional[List[Dict]] = None,
                     theme: str = "light", title: str = "Error map") -> Figure:
    """Usable region from the test points (top view): mean |e| / gauge % per point as filled color bands (light = close
    to the true force, dark = large error); the band edges are the k values in `err_levels` (continued in steps of the
    last gap), so each band is one usable-region step. No contour lines (2026-10-07 user request).
    Values are interpolated linearly between the points (inside their triangles only, never extrapolated). Points with too
    few samples are hollow and not used. Below: per k, points / share of the tested area with mean |e| % < k.
    The error distribution behind each value is in `points.png`."""
    t = THEMES[theme]
    f = _fig(8, 7.8, theme)
    ks = sorted(float(k) for k in settings.get("err_levels", [5, 10, 20, 30]))
    by_id, use = _point_rows(points, point_rows)
    x = np.array([p.x_mm for p in use], dtype=float)
    y = np.array([p.y_mm for p in use], dtype=float)
    ae = np.array([by_id[p.id]["abs_err_pct"] for p in use], dtype=float)
    tri = triangulation(x, y)
    ax = f.add_subplot(1, 1, 1)
    _plain_sensor(ax, g, theme)
    if tri is not None:
        bounds = [0.0] + _ext_levels(ks, float(ae.max()))
        cs = ax.tricontourf(tri, ae, levels=bounds, cmap=matplotlib.colormaps["YlOrRd"], alpha=0.85, zorder=1,
                            extend="max")
        cb = f.colorbar(cs, ax=ax, shrink=0.7, ticks=bounds[::2] if len(bounds) > 12 else bounds)
        cb.set_label("Mean |e| / gauge %", color=t["fg"])
        cb.ax.tick_params(colors=t["fg"], labelsize=8)
    else:
        ax.text(0.5, 0.02, "The map needs 3 or more points with enough samples, not all in a line",
                transform=ax.transAxes, ha="center", va="bottom", color=t["muted"], fontsize=9)
    for p in points:
        r = by_id.get(p.id, {})
        ok = p in use
        if ok:
            ax.scatter([p.x_mm], [p.y_mm], s=26, color=t["fg"], linewidths=0, zorder=4)
        else:
            ax.scatter([p.x_mm], [p.y_mm], s=26, facecolors="none", edgecolors=t["muted"], linewidths=1.0, zorder=4)
        v = r.get("abs_err_pct")
        txt = p.id + ("" if not ok or v is None else f" {v:.0f}%")
        ax.annotate(txt, (p.x_mm, p.y_mm), xytext=(4, 3), textcoords="offset points", fontsize=7,
                    color=t["fg"] if ok else t["muted"], zorder=5)
    ax.set_title("Mean |error| relative to the true force (band edges = k %)", color=t["fg"], fontsize=10)
    if summary:
        lines = [f"mean |e| < {r['k_pct']:g}%: {r['points_ok']}/{r['points']} points"
                 + ("" if r["area_ok_pct"] is None else f", {r['area_ok_pct']:.0f}% of the tested area") for r in summary]
        f.supxlabel("\n".join(lines), color=t["fg"], fontsize=9)
    f.suptitle(title + "  (top view, up = rounded tip; hollow = too few samples, not used;\nvalues between points are "
               "interpolated; error distributions in points.png)", color=t["fg"], fontsize=10)
    return f


def points_figure(g: Geometry, points, samples: pd.DataFrame, point_rows: pd.DataFrame, settings: Dict,
                  theme: str = "light", title: str = "Test points") -> Figure:
    """Error distribution per test point, grouped by zone (`points.load_zones`, 2026-10-07 user request).

    One panel per zone, laid out like the sensor (row 0 = tip; columns = left side, top, right side). x = gauge N,
    y = error e = |F| − gauge in N (0 = reads the true force; 2026-10-07 user request, was 100·e / gauge %), for contact
    samples; dotted lines y = ±x (e = ±gauge). Each point has its own color (stable samples); gray = not stable. Legend: mean |e| / gauge % (the main
    figure) and n per point; panel title: the same over all the zone's points. The small drawing marks the zone's taxels
    and its points. Zones without points stay in the grid as an untested drawing; points in no zone go to an extra
    "Other" panel. The y axis is shared and covers 99 % of the stable samples."""
    t = THEMES[theme]
    by_id = {r["id"]: r for r in point_rows.to_dict(orient="records")}
    zones = list(load_zones(g.label))
    groups = [(z, sorted((p for p in points if zone_of(zones, p) is z), key=lambda p: _point_num(p.id))) for z in zones]
    rest = [p for p in points if zone_of(zones, p) is None]
    nrow = max([z.row for z in zones], default=-1) + 1
    ncol = max([z.col for z in zones], default=0) + 1
    cells = [(z.row, z.col, z, ps) for z, ps in groups]
    if rest or not zones:
        cells.append((nrow, 0, None, sorted(rest, key=lambda p: _point_num(p.id))))
        nrow += 1
    f = _fig(5.6 * ncol, 3.4 * nrow + 0.6, theme)
    outer = f.add_gridspec(nrow, ncol, hspace=0.45)
    pct_min = float(settings.get("pct_min_N", 0.0))
    ct = samples[(samples["contact"] == 1) & samples["point"].astype(str).isin([p.id for p in points])]
    xhi = max(float(settings.get("max_N", 15)) * 0.7, float(ct["gauge_N"].max()) if len(ct) else 0.0) * 1.05
    stv = ct.loc[ct["stable"] == 1, "error_N"].to_numpy(dtype=float)
    stv = stv[np.isfinite(stv)]
    lo = min(-1.0, float(np.percentile(stv, 0.5))) if len(stv) else -1.0
    hi = max(1.0, float(np.percentile(stv, 99.5))) if len(stv) else 1.0
    pad = 0.08 * (hi - lo)
    for row, col, z, ps in cells:
        inner = outer[row, col].subgridspec(1, 2, width_ratios=[1, 2.4], wspace=0.05)
        a0 = f.add_subplot(inner[0, 0])
        _plain_sensor(a0, g, theme)
        if z is not None:
            zt = list(z.taxels)
            a0.scatter(g.taxels[zt, 0], g.taxels[zt, 1], s=60, color=t["point"], alpha=0.25, linewidths=0, zorder=1)
        name = "Other" if z is None else z.name
        if not ps:
            a0.set_title(f"{name}: no test points", fontsize=9, color=t["muted"], loc="left")
            continue
        ax = f.add_subplot(inner[0, 1])
        _style(ax, theme)
        ax.axhline(0.0, color=t["zero"], linewidth=0.8, linestyle="--")
        for k in (1.0, -1.0):  # e = ±gauge: |F| = 2 × gauge / |F| = 0 (2026-10-08 user request)
            ax.axline((0.0, 0.0), slope=k, color=t["zero"], linewidth=0.8, linestyle=":")
        zs = ct[ct["point"].astype(str).isin([p.id for p in ps])]
        off = zs["stable"].to_numpy() != 1
        ax.scatter(zs["gauge_N"].to_numpy(dtype=float)[off], zs["error_N"].to_numpy(dtype=float)[off], s=3,
                   color=t["other"], alpha=0.3, linewidths=0, rasterized=True)
        for i, p in enumerate(ps):
            c = PALETTE[i % len(PALETTE)]
            a0.scatter([p.x_mm], [p.y_mm], s=36, color=c, edgecolors=t["bg"], linewidths=0.6, zorder=3)
            sub = zs[(zs["point"].astype(str) == p.id) & (zs["stable"] == 1)]
            r = by_id.get(p.id, {})
            lab = (f"{p.id}  {_fmt(r.get('abs_err_pct'), '%', 0)}  n {r.get('n_fit') or 0}"
                   + ("" if r.get("enough") else " (too few)"))
            ax.scatter(sub["gauge_N"], sub["error_N"], s=5, color=c, alpha=0.6, linewidths=0, rasterized=True, label=lab)
        st = zs[(zs["stable"] == 1) & (zs["gauge_N"] >= pct_min)]
        _, ap = error_pct(st["gauge_N"].to_numpy(dtype=float), st["error_N"].to_numpy(dtype=float))
        ax.set_xlim(0, xhi)
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_title(f"{name}: mean |e| {_fmt(ap, '%', 0)}, n {len(st)}", fontsize=9, color=t["fg"])
        leg = ax.legend(loc="best", fontsize=7, frameon=False, labelcolor=t["fg"], markerscale=3, handletextpad=0.3)
        leg.set_zorder(5)
    f.suptitle(title + " by zone  (x = gauge N, y = error |F| − gauge N; colored = stable samples per point, "
               "gray = not stable, dashed = 0,\ndotted = y = ±x (error ±100 % of gauge); legend: mean |e| / gauge %, n)",
               color=t["fg"], fontsize=11)
    return f


def _point_num(pid: str) -> int:
    """P12 → 12 (ids are P1, P2, ... in order of creation)."""
    d = "".join(ch for ch in pid if ch.isdigit())
    return int(d) if d else 0


AXIS_COLORS = {"X": PALETTE[0], "Y": PALETTE[1], "Z": PALETTE[2]}


def noload_figure(samples: pd.DataFrame, result: Dict, theme: str = "light", title: str = "No-load residual") -> Figure:
    """Sensor residual per axis: resultant X, Y, Z in N, not |F| (2026-10-07 user request). The gauge is not shown.

    Top: over the recording, the samples with nothing pressed and held still (`released`) per axis vs time since the first
    sample; the no-load checks before / after the test as diamonds at their times (an attached sensor-only log is marked
    "log"); shaded = the end-of-recording stretch hands off.
    Bottom: per-axis means before the test / at the end of the recording / after the test (values written on the bars)."""
    t = THEMES[theme]
    f = _fig(12, 7.6, theme)
    gs = f.add_gridspec(2, 1, height_ratios=[1.6, 1], hspace=0.38)
    ax = f.add_subplot(gs[0])
    _style(ax, theme)
    t0 = float(samples["t_unix_s"].min()) if len(samples) else 0.0
    rel = samples[samples["released"] == 1] if "released" in samples else samples.iloc[0:0]
    for axis, col in zip("XYZ", ("Fx_N", "Fy_N", "Fz_N")):
        ax.scatter(rel["t_unix_s"] - t0, rel[col], s=5, color=AXIS_COLORS[axis], alpha=0.6, linewidths=0,
                   rasterized=True, label=f"{axis}")
    ax.axhline(0.0, color=t["zero"], linewidth=0.8, linestyle="--")
    er = result.get("end_residual")
    if er and er.get("t_unix_s") is not None:
        a = float(er["t_unix_s"]) - t0
        ax.axvspan(a, a + float(er["seconds"]), color=t["muted"], alpha=0.15, zorder=0)
    bars = []
    for key, name in (("noload_check", "Before the test"), ("end_residual", "End of the recording"),
                      ("noload_after", "After the test")):
        e = result.get(key)
        xyz = e.get("sensor_mean_N") if e else None
        bars.append((name, xyz if xyz and all(v is not None for v in xyz) else None))
        if key == "end_residual" or not xyz or e.get("t_unix_s") is None:
            continue
        tm = float(e["t_unix_s"]) - t0 + float(e.get("seconds") or 0.0) / 2
        for axis, v in zip("XYZ", xyz):
            ax.scatter([tm], [v], s=70, marker="D", color=AXIS_COLORS[axis], edgecolors=t["fg"], linewidths=0.8, zorder=4)
        ax.annotate(name.replace(" the test", "") + (" (log)" if e.get("source") else ""), (tm, max(xyz)),
                    xytext=(0, 9), textcoords="offset points", ha="center", fontsize=8, color=t["fg"])
    ax.set_xlabel("Time since the recording started (s)")
    ax.set_ylabel("Sensor resultant (N)")
    ax.set_title("Nothing pressed and held still (dots), no-load checks (diamonds), end of the recording (shaded)",
                 fontsize=10, color=t["fg"])
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=t["fg"], markerscale=3)
    a2 = f.add_subplot(gs[1])
    _style(a2, theme)
    x = np.arange(len(bars))
    w = 0.26
    for i, axis in enumerate("XYZ"):
        vals = [b[1][i] if b[1] else np.nan for b in bars]
        a2.bar(x + (i - 1) * w, np.nan_to_num(vals), w, color=AXIS_COLORS[axis], label=axis)
        for xi, v in zip(x + (i - 1) * w, vals):
            a2.annotate("-" if not np.isfinite(v) else f"{v:+.2f}", (xi, 0 if not np.isfinite(v) else v),
                        xytext=(0, 3 if not np.isfinite(v) or v >= 0 else -10), textcoords="offset points",
                        ha="center", fontsize=8, color=t["fg"])
    a2.axhline(0.0, color=t["zero"], linewidth=0.8)
    a2.set_xticks(x, [b[0] for b in bars])
    a2.set_ylabel("Mean (N)")
    a2.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=t["fg"])
    f.suptitle(title + " per axis  (sensor resultant X, Y, Z; the gauge is not shown)", color=t["fg"], fontsize=11)
    return f


def sensors_compare_figure(sensor_rows: pd.DataFrame, theme: str = "light") -> Figure:
    """HAND: bias, RMSE and slope bars per sensor."""
    t = THEMES[theme]
    f = _fig(10, 3.6, theme)
    labels = list(sensor_rows["name"])
    x = np.arange(len(labels))
    for i, (col, name) in enumerate((("bias_N", "bias (N)"), ("rmse_N", "RMSE (N)"), ("slope", "Slope a"))):
        ax = f.add_subplot(1, 3, i + 1)
        _style(ax, theme)
        v = pd.to_numeric(sensor_rows[col], errors="coerce").to_numpy(dtype=float)
        ax.bar(x, np.nan_to_num(v), color=[PALETTE[j % len(PALETTE)] for j in range(len(x))])
        ax.set_xticks(x, labels)
        ax.set_title(name, fontsize=10)
        if col == "slope":
            ax.axhline(1.0, color=t["zero"], linewidth=0.8)
    return f


def crosstalk_figure(ct: pd.DataFrame, labels: List[str], theme: str = "light") -> Figure:
    """HAND: pressed sensor (row) × other sensor (col), other |F| P95 / pressed sensor |F| (%)."""
    t = THEMES[theme]
    f = _fig(5.5, 4.6, theme)
    ax = f.add_subplot(1, 1, 1)
    n = len(labels)
    m = np.full((n, n), np.nan)
    for r in ct.to_dict(orient="records"):
        if r["pressed"] in labels and r["other"] in labels and r["p95_ratio_pct"] is not None:
            m[labels.index(r["pressed"]), labels.index(r["other"])] = r["p95_ratio_pct"]
    im = ax.imshow(m, cmap=matplotlib.colormaps["magma"], vmin=0)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, "-" if not np.isfinite(m[i, j]) else f"{m[i, j]:.1f}%", ha="center", va="center",
                    color="#ffffff" if not np.isfinite(m[i, j]) or m[i, j] < np.nanmax(m) * 0.6 else "#000000",
                    fontsize=9)
    ax.set_xticks(range(n), labels)
    ax.set_yticks(range(n), labels)
    ax.set_xlabel("Other sensor", color=t["fg"])
    ax.set_ylabel("Pressed sensor", color=t["fg"])
    ax.tick_params(colors=t["fg"])
    ax.set_facecolor(t["bg"])
    ax.set_title("Crosstalk: other sensor |F| P95 / pressed sensor |F|", color=t["fg"], fontsize=10)
    f.colorbar(im, ax=ax, shrink=0.8).ax.tick_params(colors=t["fg"])
    return f
