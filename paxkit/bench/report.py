"""Analysis output files (plan 4.2): samples.csv, metrics.csv, crosstalk.csv (HAND), result.json, plots/*.png, report.html.

Figures (2026-10-07, all location results come from the tester's test points): overall_error.png, error_map.png
(sensitivity error % with ± k contours + scatter map, usable-region summary), points.png (gauge vs |F| per test point).

Re-analysis overwrites them. Analyzing the same folder twice gives identical content except `analyzed_at`
(rounded values, fixed column order). Figures are saved in the light theme (the GUI redraws them in the dark theme).
"""
from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from . import plots
from .plots import lag_text
from ..device.geometry import load_geometry

ROUND = {"t_unix_s": 6}
DEFAULT_ND = 4

METRIC_LABELS = [
    ("name", "Group"), ("n_fit", "n (error %)"), ("abs_err_pct", "Mean |e|/gauge %"), ("bias_pct", "Mean e/gauge %"),
    ("rmse_N", "RMSE"), ("gauge_min_N", "Gauge min"), ("gauge_max_N", "Gauge max"), ("n", "Stable n"),
    ("bias_N", "bias"), ("sd_N", "SD"), ("mae_N", "MAE"), ("p95_abs_N", "P95 |e|"),
    ("max_abs_N", "Max |e|"), ("rmse_pct_fs", "RMSE %FS"),
    ("gain", "Gain (ref.)"), ("gain_err_pct", "Gain error % (ref.)"), ("resid_sd_N", "Scatter (ref.)"),
    ("slope", "Slope"), ("intercept", "Intercept"), ("r2", "R²"),
    ("n_contact", "Contact n"), ("bias_contact_N", "Contact bias"), ("sd_contact_N", "Contact SD"),
    ("rmse_contact_N", "Contact RMSE"),
    ("noload_mean_N", "No-load mean"), ("noload_max_N", "No-load max"),
]


def _round_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(ROUND.get(c, DEFAULT_ND))
    return out


def _csv(df: pd.DataFrame, path: Path) -> None:
    path.write_bytes(_round_df(df).to_csv(index=False, lineterminator="\n").encode("utf-8"))


def point_figures(res, theme: str = "light") -> List[Tuple[str, str, object]]:
    """Error map + per-point trends of a single-sensor result with test points (empty otherwise)."""
    pts = res.points
    if not pts or len(res.sensors) != 1:
        return []
    g = load_geometry(res.sensors[0]["model"]) if res.sensors[0].get("model") else None
    if g is None:
        return []
    rows = res.metrics[res.metrics["scope"] == "point"]
    return [("error_map.png", "Error map",
             plots.error_map_figure(g, pts, rows, res.settings, res.result.get("error_map"), theme)),
            ("points.png", "Test points", plots.points_figure(g, pts, res.samples, rows, res.settings, theme))]


def figures(res, theme: str = "light") -> List[Tuple[str, str, object]]:
    """List of (file path relative to plots/, title, Figure). The GUI uses this list too."""
    out = []
    multi = len(res.sensors) > 1
    for s in res.sensors:
        lab = s["label"]
        sub = res.sensor_samples(lab)
        mrows = res.sensor_metrics(lab)
        top = mrows[mrows["scope"] == ("sensor" if multi else "overall")]
        row = top.iloc[0].to_dict() if len(top) else {}
        prefix = f"{lab}/" if multi else ""
        name = f"{lab} " if multi else ""
        title = f"{name}Overall error".strip()
        out.append((prefix + "overall_error.png", title,
                    plots.overall_error_figure(sub, row, res.result, s, theme, title=title)))
    out += point_figures(res, theme)
    if len(res.sensors) == 1:
        out.append(("noload.png", "No-load residual", plots.noload_figure(res.samples, res.result, theme)))
    if multi:
        srows = res.metrics[res.metrics["scope"] == "sensor"]
        out.insert(0, ("overall_error.png", "Overall error (all sensors)",
                       plots.overall_error_figure(res.samples, res.metrics.iloc[0].to_dict(), res.result, None, theme,
                                                  title="Overall error (all sensors)")))
        out.append(("sensors_compare.png", "Sensor comparison", plots.sensors_compare_figure(srows, theme)))
        if res.crosstalk is not None:
            out.append(("crosstalk.png", "Crosstalk", plots.crosstalk_figure(res.crosstalk, [s["label"] for s in res.sensors], theme)))
    return out


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=fig.get_facecolor())
    return buf.getvalue()


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    if isinstance(v, (bool, np.bool_)):
        return "enough" if v else "not enough"
    if isinstance(v, float):
        return f"{v:.3f}"
    return html.escape(str(v))


def metrics_html(df: pd.DataFrame) -> str:
    head = "".join(f"<th>{html.escape(lab)}</th>" for _, lab in METRIC_LABELS)
    body = []
    for r in df.to_dict(orient="records"):
        name = str(r["name"])
        cls = "" if r.get("enough", True) or r["scope"] != "point" else ' class="thin"'
        cells = "".join(f"<td>{_fmt(name if k == 'name' else r.get(k))}</td>" for k, _ in METRIC_LABELS)
        body.append(f"<tr{cls}>{cells}</tr>")
    return f"<table><tr>{head}</tr>{''.join(body)}</table>"


def write_outputs(res) -> None:
    folder: Path = res.folder
    _csv(res.samples, folder / "samples.csv")
    _csv(res.metrics, folder / "metrics.csv")
    if res.crosstalk is not None:
        _csv(res.crosstalk, folder / "crosstalk.csv")
    (folder / "result.json").write_bytes(
        json.dumps(res.result, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    pdir = folder / "plots"
    pdir.mkdir(exist_ok=True)
    imgs: Dict[str, bytes] = {}
    figs = figures(res, "light")
    keep = {(pdir / rel).resolve() for rel, _, _ in figs}
    for old in pdir.rglob("*.png"):   # plots/ is all analysis output: drop figures an earlier analysis made
        if old.resolve() not in keep:
            old.unlink()
    for rel, _title, fig in figs:
        p = pdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _png(fig)
        p.write_bytes(data)
        imgs[rel] = data
    (folder / "report.html").write_bytes(_report_html(res, figs, imgs).encode("utf-8"))


def _location_html(r: Dict) -> str:
    pts = r.get("points") or []
    if not pts:
        return "No test points selected: overall results only."
    out = (f"Test points selected by the tester ({len(pts)}): "
           + html.escape(", ".join(f"{p['id']} ({p['x_mm']:.1f}, {p['y_mm']:.1f} mm)" for p in pts)) + ".")
    for e in r.get("error_map") or []:
        out += (f"<br>Usable (mean |e|/gauge < {e['k_pct']:g}%): "
                f"{e['points_ok']} / {e['points']} points"
                + ("" if e.get("area_ok_pct") is None else
                   f", {e['area_ok_pct']:.0f}% of the tested area ({e['area_ok_mm2']:.0f} / {e['area_mm2']:.0f} mm²)"))
    return out


def noload_lines(r: Dict) -> List[str]:
    """No-load residual before the test, after it (post-test check) and at the end of the recording (from the data)."""
    out = []
    n = lambda v: "-" if v is None else f"{v:.2f} N"
    ax = lambda e: ("" if not e.get("sensor_mean_N") or None in e["sensor_mean_N"] else
                    " (X {:+.2f}, Y {:+.2f}, Z {:+.2f})".format(*e["sensor_mean_N"]))
    for key, name in (("noload_check", "Before the test"), ("noload_after", "After the test")):
        e = r.get(key)
        if e:
            w = ", ".join(e.get("warnings") or [])
            src = (f"; from {e['source']}, {e['after_stop_s']:g} s after the stop"
                   if e.get("source") and e.get("after_stop_s") is not None else "")
            out.append(f"{name}: sensor |F| {n(e.get('sensor_F_mean_N'))}{ax(e)}, gauge {n(e.get('gauge_mean_N'))}"
                       + (f" ({w}{src})" if w or src else ""))
        else:
            out.append(f"{name}: not done")
    e = r.get("end_residual")
    if e:
        out.append(f"End of the recording (last {e['seconds']:.1f} s hands off): sensor |F| mean "
                   f"{n(e['sensor_F_mean_N'])}{ax(e)}, max {n(e['sensor_F_max_N'])}, gauge {n(e['gauge_mean_N'])}")
    return out


def _noload_html(r: Dict) -> str:
    return "<br>".join(html.escape(s) for s in noload_lines(r))


def _report_html(res, figs, imgs) -> str:
    r = res.result
    sens = "".join(
        f"<li>{html.escape(s['label'])}: {html.escape(str(s.get('model')))} (channel {s['channel']}, slot {s['slot']}), "
        f"{s['frames']} frames, {s['rate_hz']} Hz, residual lag {html.escape(lag_text(s))}</li>"
        for s in r["sensors"])
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in r["warnings"]) or "<li>None</li>"
    c = r["counts"]
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{html.escape(r['folder'])}</title><style>",
        "body{font-family:sans-serif;margin:24px;color:#222} table{border-collapse:collapse;font-size:12px}"
        "td,th{border:1px solid #ccc;padding:3px 6px;text-align:right} th{background:#f2f2f2}"
        "td:first-child{text-align:left} tr.thin td{color:#999} img{max-width:100%;border:1px solid #eee;margin:8px 0}"
        "</style></head><body>",
        f"<h1>Gauge test result — {html.escape(r['folder'])}</h1>",
        f"<p>Label {html.escape(str(r.get('label')))}, model {html.escape(str(r.get('model')))}, "
        f"recorded {r.get('duration_s')} s, analyzed {html.escape(r['analyzed_at'])} "
        f"(paxkit {html.escape(r['paxkit'])}). No pass/fail judgement is made.</p>",
        f"<p>Gauge samples {c['gauge_samples']} ({r['gauge']['outliers']} outliers excluded), paired {c['paired']}, "
        f"contact {c['contact']}, stable {c['stable']}, no-load {c['noload']}"
        + (f", simultaneous contact excluded {c['simultaneous']}" if c.get("simultaneous") else "") + "</p>",
        f"<h2>Sensors</h2><ul>{sens}</ul>",
        "<h2>Test points</h2><p>" + _location_html(r) + "</p>",
        "<h2>No-load check</h2><p>" + _noload_html(r) + "</p>",
        f"<h2>Warnings</h2><ul>{warn}</ul>",
        "<h2>Metrics (error e = |F| − gauge, N; stable samples except the contact columns; "
        "main: mean |e|/gauge % over stable samples ≥ pct_min_N (n (error %)); "
        "reference: |F| = gain·gauge, gain error % = 100·(gain − 1), scatter = SD around that line)</h2>",
        metrics_html(res.metrics),
    ]
    for rel, title, _ in figs:
        parts.append(f"<h2>{html.escape(title)}</h2><img alt='{html.escape(rel)}' "
                     f"src='data:image/png;base64,{base64.b64encode(imgs[rel]).decode('ascii')}'>")
    s = r["settings"]
    parts.append("<h2>Analysis settings</h2><pre>" + html.escape(json.dumps(s, ensure_ascii=False, indent=1)) + "</pre>")
    parts.append("</body></html>\n")
    return "".join(parts)
