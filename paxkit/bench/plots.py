"""결과 그림 (계획 P6-3): matplotlib `Figure`를 만들어 파일(PNG)과 GUI에 같이 쓴다.

pyplot을 쓰지 않는다 (GUI 스레드·백엔드와 무관하게 만들기 위해 `Figure`를 직접 생성).
theme: "light" = 파일·보고서용, "dark" = GUI용 (내용은 같고 색만 다름).

센서 그림은 위에서 본 모습 (x–y 투영, 오른쪽 = +x, 위 = +y = 둥근 끝).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import matplotlib
import numpy as np
import pandas as pd
from matplotlib import cm, font_manager
from matplotlib.colors import Normalize
from matplotlib.figure import Figure

from .metrics import binned
from .zones import ZoneSet

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#222222", "muted": "#888888", "grid": "#dddddd", "surface": "#c8c8c8",
              "point": "#1f77b4", "band": "#ff7f0e", "zero": "#444444", "thin": "#bbbbbb", "cmap": "viridis"},
    "dark": {"bg": "#1e1f22", "fg": "#dcdcdc", "muted": "#8a8a8a", "grid": "#3a3b3f", "surface": "#4a4b50",
             "point": "#4fa3e0", "band": "#ffa040", "zero": "#bbbbbb", "thin": "#555555", "cmap": "viridis"},
}
# 한글 글꼴: OS마다 있는 것 중 첫 번째 (없으면 기본 글꼴, 한글이 네모로 보임)
KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR",
                "NanumGothic", "UnDotum"]


def _setup_fonts() -> None:
    have = {f.name for f in font_manager.fontManager.ttflist}
    found = [n for n in KOREAN_FONTS if n in have]
    if found:
        matplotlib.rcParams["font.family"] = [found[0], "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False


_setup_fonts()

ZONE_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#9a6324"]


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
    """모든 오차 그래프가 같이 쓰는 축 범위 (안정 샘플 기준)."""
    st = samples[samples["stable"] == 1]
    xmax = max(float(settings.get("max_N", 15)), float(st["gauge_N"].max()) if len(st) else 0.0) * 1.05
    e = st["error_N"].to_numpy(dtype=float)
    lim = max(0.5, float(np.percentile(np.abs(e), 99.5)) * 1.15) if len(e) else 1.0
    return (0.0, xmax), (-lim, lim)


def draw_error(ax, samples: pd.DataFrame, settings: Dict, theme: str, xlim=None, ylim=None,
               title: Optional[str] = None, small: bool = False) -> None:
    """오차 그래프: x = 게이지 N, y = |F| − 게이지 N. 안정 샘플 점 + 구간 평균 ±1 SD 띠 + 0 선."""
    t = THEMES[theme]
    _style(ax, theme)
    st = samples[samples["stable"] == 1]
    g = st["gauge_N"].to_numpy(dtype=float)
    e = st["error_N"].to_numpy(dtype=float)
    ax.axhline(0, color=t["zero"], linewidth=0.8)
    ax.scatter(g, e, s=4 if small else 6, color=t["point"], alpha=0.25 if len(g) > 300 else 0.45,
               linewidths=0, rasterized=True)
    c, m, sd, _ = binned(g, e, float(settings.get("bin_N", 1.0)))
    if len(c):
        ax.fill_between(c, m - sd, m + sd, color=t["band"], alpha=0.25, linewidth=0)
        ax.plot(c, m, color=t["band"], linewidth=1.6 if not small else 1.2, marker="o", markersize=2.5)
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)
    if not small:
        ax.set_xlabel("게이지 (N)")
        ax.set_ylabel("오차 |F| − 게이지 (N)")
    if title:
        ax.set_title(title, fontsize=9 if small else 11)


def _metrics_text(row: Dict, result: Dict, sensor: Optional[Dict]) -> str:
    lines = [
        f"안정 샘플 n = {row.get('n', 0)}",
        f"게이지 범위 {_fmt(row.get('gauge_min_N'), '', 1)} ~ {_fmt(row.get('gauge_max_N'), ' N', 1)}",
        f"bias {_fmt(row.get('bias_N'), ' N')}",
        f"SD {_fmt(row.get('sd_N'), ' N')}",
        f"RMSE {_fmt(row.get('rmse_N'), ' N')}  ({_fmt(row.get('rmse_pct_fs'), ' %FS', 2)})",
        f"MAE {_fmt(row.get('mae_N'), ' N')}",
        f"P95 |e| {_fmt(row.get('p95_abs_N'), ' N')}",
        f"최대 |e| {_fmt(row.get('max_abs_N'), ' N')}",
        f"|F| = {_fmt(row.get('slope'), '', 4)}·게이지 {'+' if (row.get('intercept') or 0) >= 0 else '−'} "
        f"{_fmt(abs(row.get('intercept') or 0), ' N')}",
        f"R² {_fmt(row.get('r2'), '', 5)}",
        "",
        f"접촉 샘플 n = {row.get('n_contact', 0)}, RMSE {_fmt(row.get('rmse_contact_N'), ' N')}",
        f"Z만: bias {_fmt(row.get('bias_z_N'), ' N')}, RMSE {_fmt(row.get('rmse_z_N'), ' N')}",
    ]
    if row.get("n_noload") is not None:
        lines.append(f"무부하 잔류 |F| 평균 {_fmt(row.get('noload_mean_N'), ' N')}, 최대 {_fmt(row.get('noload_max_N'), ' N')}")
    if sensor is not None:
        lag = sensor.get("lag_s")
        lines.append("지연(센서−게이지) " + ("-" if lag is None else f"{lag * 1e3:+.0f} ms (r {sensor.get('lag_r'):.2f}, 보정 안 함)"))
    return "\n".join(lines)


def overall_error_figure(samples: pd.DataFrame, row: Dict, result: Dict, sensor: Optional[Dict] = None,
                         theme: str = "light", title: str = "전체 오차") -> Figure:
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
    """센서 그림: 표면 점(회색) + taxel (구역 색 / 값 색 / 강조)."""
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
        ax.scatter(g.taxels[on, 0], g.taxels[on, 1], s=taxel_size, color=ZONE_COLORS[highlight % 9], linewidths=0)
    else:
        cols = [ZONE_COLORS[k % 9] if k >= 0 else t["thin"] for k in tz]
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
                    title: str = "구역별 RMSE") -> Figure:
    """센서 그림 위에 구역별 색 = RMSE (데이터 부족 구역은 흐리게), CoP 샘플 점."""
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
    ax.set_title(title + "  (회색 = 데이터 부족, 점 = 안정 샘플 CoP)", color=t["fg"], fontsize=10)
    return f


def zones_figure(zs: ZoneSet, samples: pd.DataFrame, zone_rows: pd.DataFrame, settings: Dict,
                 theme: str = "light", title: str = "구역별 오차") -> Figure:
    """구역별 오차 그래프 격자 (세로 3 × 가로 3). 칸마다 [작은 센서 그림(구역 강조) | 오차 그래프], 축 범위 공통."""
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
        tt = f"{z.name}  n {r.get('n', 0)}  bias {_fmt(r.get('bias_N'), '', 2)}  RMSE {_fmt(r.get('rmse_N'), '', 2)}"
        draw_error(a1, sub, settings, theme, xlim, ylim, title=tt, small=True)
        if not r.get("enough", False):
            a1.text(0.5, 0.5, "데이터 부족", transform=a1.transAxes, ha="center", va="center",
                    color=t["muted"], fontsize=12, alpha=0.8)
    f.suptitle(title + "  (x = 게이지 N, y = 오차 N, 모든 칸 같은 축)", color=t["fg"], fontsize=11)
    return f


def sensors_compare_figure(sensor_rows: pd.DataFrame, theme: str = "light") -> Figure:
    """HAND: 센서별 bias·RMSE·기울기 막대."""
    t = THEMES[theme]
    f = _fig(10, 3.6, theme)
    labels = list(sensor_rows["name"])
    x = np.arange(len(labels))
    for i, (col, name) in enumerate((("bias_N", "bias (N)"), ("rmse_N", "RMSE (N)"), ("slope", "기울기 a"))):
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
    """HAND: 누른 센서(행) × 다른 센서(열)의 |F| P95 / 누른 센서 |F| (%)."""
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
    ax.set_xlabel("다른 센서", color=t["fg"])
    ax.set_ylabel("누른 센서", color=t["fg"])
    ax.tick_params(colors=t["fg"])
    ax.set_facecolor(t["bg"])
    ax.set_title("간섭: 다른 센서 |F| P95 / 누른 센서 |F|", color=t["fg"], fontsize=10)
    f.colorbar(im, ax=ax, shrink=0.8).ax.tick_params(colors=t["fg"])
    return f


def zone_check_figure(zs: ZoneSet, theme: str = "light") -> Figure:
    """구역 정의 확인용: taxel 번호와 구역 색, 구역 이름."""
    t = THEMES[theme]
    f = _fig(7, 8, theme)
    ax = f.add_subplot(1, 1, 1)
    draw_sensor(ax, zs, theme, taxel_size=160)
    g = zs.geometry
    for i, p in enumerate(g.taxels):
        ax.text(p[0], p[1], str(i), ha="center", va="center", fontsize=7, color="#ffffff")
    handles = [ax.scatter([], [], s=60, color=ZONE_COLORS[k % 9], label=f"{z.name} ({len(z.taxels)})")
               for k, z in enumerate(zs.zones)]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8, frameon=False,
              labelcolor=t["fg"])
    ax.set_title(f"{zs.model} 구역 (위에서 본 모습, 위 = 둥근 끝, 오른쪽 = +x)", color=t["fg"], fontsize=10)
    f.text(0.02, 0.01, zs.rule, color=t["muted"], fontsize=7, wrap=True)
    return f
