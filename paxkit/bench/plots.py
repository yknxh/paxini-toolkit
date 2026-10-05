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
from matplotlib import cm
from matplotlib.colors import Normalize
from matplotlib.figure import Figure

from .metrics import binned
from .zones import ZoneSet

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#222222", "muted": "#888888", "grid": "#dddddd", "surface": "#c8c8c8",
              "point": "#1f77b4", "band": "#ff7f0e", "zero": "#444444", "thin": "#bbbbbb", "cmap": "viridis",
              "other": "#aaaaaa"},
    "dark": {"bg": "#1e1f22", "fg": "#dcdcdc", "muted": "#8a8a8a", "grid": "#3a3b3f", "surface": "#4a4b50",
             "point": "#4fa3e0", "band": "#ffa040", "zero": "#bbbbbb", "thin": "#555555", "cmap": "viridis",
             "other": "#6e6e6e"},
}
ZONE_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#9a6324"]


def zone_color(zs: ZoneSet, k: int) -> str:
    """Zone color follows the grid position (row, col), so the same place keeps its color if zones change."""
    z = zs.zones[k]
    return ZONE_COLORS[(z.row * 3 + z.col) % len(ZONE_COLORS)]


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


def draw_sensor(ax, zs: ZoneSet, theme: str, *, highlight: Optional[int] = None,
                zone_values: Optional[np.ndarray] = None, vmin=None, vmax=None, labels: bool = False,
                label_text: Optional[List[str]] = None, taxel_size: float = 30, cop=None, thin=None) -> None:
    """Sensor drawing: surface points (gray) + taxels (zone color / value color / highlight)."""
    t = THEMES[theme]
    g = zs.geometry
    ax.set_facecolor(t["bg"])
    ax.scatter(g.surface[:, 0], g.surface[:, 1], s=2, color=t["surface"], linewidths=0)
    tz = zs.taxel_zone()
    if zone_values is not None:
        cmap = matplotlib.colormaps[t["cmap"]]
        norm = Normalize(vmin=vmin, vmax=vmax)
        cols = []
        for k in tz:
            v = zone_values[k] if k >= 0 else np.nan
            cols.append(t["thin"] if not np.isfinite(v) else cmap(norm(v)))
        ax.scatter(g.taxels[:, 0], g.taxels[:, 1], s=taxel_size, c=cols, linewidths=0.4, edgecolors=t["bg"])
    elif highlight is not None:
        on = tz == highlight
        ax.scatter(g.taxels[~on, 0], g.taxels[~on, 1], s=taxel_size * 0.5, color=t["thin"], linewidths=0)
        ax.scatter(g.taxels[on, 0], g.taxels[on, 1], s=taxel_size, color=zone_color(zs, highlight), linewidths=0)
    else:
        cols = [zone_color(zs, k) if k >= 0 else t["thin"] for k in tz]
        ax.scatter(g.taxels[:, 0], g.taxels[:, 1], s=taxel_size, c=cols, linewidths=0)
    if cop is not None and len(cop):
        ax.scatter(cop[:, 0], cop[:, 1], s=3, color=t["fg"], alpha=0.35, linewidths=0, rasterized=True)
    if labels:
        for k, z in enumerate(zs.zones):
            p = g.taxels[list(z.taxels)].mean(axis=0) if z.taxels else None
            if p is None:
                continue
            txt = label_text[k] if label_text else z.name
            faded = thin is not None and thin[k]
            ax.text(p[0], p[1], txt, ha="center", va="center", fontsize=7, color=t["fg"], alpha=0.5 if faded else 1.0,
                    bbox=dict(boxstyle="round,pad=0.2", fc=t["bg"], ec="none", alpha=0.6))
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)


def zone_map_figure(zs: ZoneSet, samples: pd.DataFrame, zone_rows: pd.DataFrame, theme: str = "light",
                    title: str = "RMSE by zone") -> Figure:
    """Sensor drawing colored by zone RMSE (zones without enough data are faded), plus CoP sample points."""
    t = THEMES[theme]
    f = _fig(6.5, 7, theme)
    ax = f.add_subplot(1, 1, 1)
    by_id = {r["id"]: r for r in zone_rows.to_dict(orient="records")}
    vals = np.array([by_id.get(z.id, {}).get("rmse_N") if by_id.get(z.id, {}).get("rmse_N") is not None else np.nan
                     for z in zs.zones], dtype=float)
    thin = np.array([not bool(by_id.get(z.id, {}).get("enough", False)) for z in zs.zones])
    vv = vals[np.isfinite(vals)]
    vmin, vmax = (0.0, float(vv.max()) if len(vv) else 1.0)
    vmax = max(vmax, 1e-3)
    text = [f"{z.name}\nRMSE {_fmt(by_id.get(z.id, {}).get('rmse_N'), '', 2)}\nn {by_id.get(z.id, {}).get('n', 0)}"
            for z in zs.zones]
    st = samples[(samples["stable"] == 1)]
    cop = st[["cop_x_mm", "cop_y_mm"]].dropna().to_numpy(dtype=float)
    draw_sensor(ax, zs, theme, zone_values=np.where(thin, np.nan, vals), vmin=vmin, vmax=vmax, labels=True,
                label_text=text, taxel_size=90, cop=cop, thin=thin)
    sm = cm.ScalarMappable(norm=Normalize(vmin, vmax), cmap=matplotlib.colormaps[t["cmap"]])
    cb = f.colorbar(sm, ax=ax, shrink=0.6)
    cb.set_label("RMSE (N)", color=t["fg"])
    cb.ax.tick_params(colors=t["fg"], labelsize=8)
    ax.set_title(title + "  (gray = not enough data, dots = CoP of stable samples)", color=t["fg"], fontsize=10)
    return f


def zones_figure(zs: ZoneSet, samples: pd.DataFrame, zone_rows: pd.DataFrame, settings: Dict,
                 theme: str = "light", title: str = "Error by zone") -> Figure:
    """Grid of per-zone error plots (at each zone's row/col, empty cells left blank).
    Each cell is [small sensor drawing (zone highlighted) | error plot], with shared axis limits."""
    t = THEMES[theme]
    rows = max(z.row for z in zs.zones) + 1
    ncol = max(z.col for z in zs.zones) + 1
    f = _fig(4.6 * ncol, 3.0 * rows + 0.4, theme)
    outer = f.add_gridspec(rows, ncol)
    xlim, ylim = error_limits(samples, settings)
    by_id = {r["id"]: r for r in zone_rows.to_dict(orient="records")}
    for k, z in enumerate(zs.zones):
        inner = outer[z.row, z.col].subgridspec(1, 2, width_ratios=[1, 2.6], wspace=0.05)
        a0 = f.add_subplot(inner[0, 0])
        draw_sensor(a0, zs, theme, highlight=k, taxel_size=10)
        a1 = f.add_subplot(inner[0, 1])
        r = by_id.get(z.id, {})
        sub = samples[samples["zone"] == z.id]
        tt = (f"{z.name}  (stable / all contact)\nn {r.get('n', 0)} / {r.get('n_contact', 0)}  "
              f"bias {_fmt(r.get('bias_N'), '', 2)} / {_fmt(r.get('bias_contact_N'), '', 2)}  "
              f"RMSE {_fmt(r.get('rmse_N'), '', 2)} / {_fmt(r.get('rmse_contact_N'), '', 2)}")
        draw_error(a1, sub, settings, theme, xlim, ylim, title=tt, small=True)
        if not r.get("enough", False):
            a1.text(0.5, 0.5, "Not enough data", transform=a1.transAxes, ha="center", va="center",
                    color=t["muted"], fontsize=12, alpha=0.8)
    f.suptitle(title + "  (x = gauge N, y = error N, same axes in every panel; blue = stable, gray = not stable, "
               "orange = stable mean ±1 SD, dashed = all contact mean)", color=t["fg"], fontsize=11)
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
        ax.bar(x, np.nan_to_num(v), color=[ZONE_COLORS[j % 9] for j in range(len(x))])
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


def zone_check_figure(zs: ZoneSet, theme: str = "light") -> Figure:
    """Zone definition check: taxel indices, zone colors and zone names."""
    t = THEMES[theme]
    f = _fig(7.6, 8, theme)
    ax = f.add_subplot(1, 1, 1)
    draw_sensor(ax, zs, theme, taxel_size=160)
    g = zs.geometry
    for i, p in enumerate(g.taxels):
        ax.text(p[0], p[1], str(i), ha="center", va="center", fontsize=7, color="#ffffff")
    handles = [ax.scatter([], [], s=60, color=zone_color(zs, k), label=f"{z.name} ({len(z.taxels)})")
               for k, z in enumerate(zs.zones)]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8, frameon=False,
              labelcolor=t["fg"])
    ax.set_title(f"{zs.model} zones (top view, up = rounded tip, right = +x)", color=t["fg"], fontsize=10)
    f.text(0.02, 0.01, zs.rule, color=t["muted"], fontsize=7, wrap=True)
    return f
