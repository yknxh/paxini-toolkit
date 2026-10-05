"""Analysis output files (plan 4.2): samples.csv, metrics.csv, crosstalk.csv (HAND), result.json, plots/*.png, report.html.

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
from .zones import load_zones

ROUND = {"t_unix_s": 6}
DEFAULT_ND = 4

METRIC_LABELS = [
    ("name", "Zone"), ("n", "Stable n"), ("gauge_min_N", "Gauge min"), ("gauge_max_N", "Gauge max"),
    ("bias_N", "bias"), ("sd_N", "SD"), ("rmse_N", "RMSE"), ("mae_N", "MAE"), ("p95_abs_N", "P95 |e|"),
    ("max_abs_N", "Max |e|"), ("rmse_pct_fs", "RMSE %FS"), ("slope", "Slope"), ("intercept", "Intercept"), ("r2", "R²"),
    ("n_contact", "Contact n"), ("bias_contact_N", "Contact bias"), ("sd_contact_N", "Contact SD"),
    ("rmse_contact_N", "Contact RMSE"), ("noload_mean_N", "No-load mean"), ("noload_max_N", "No-load max"),
]


def _round_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(ROUND.get(c, DEFAULT_ND))
    return out


def _csv(df: pd.DataFrame, path: Path) -> None:
    path.write_bytes(_round_df(df).to_csv(index=False, lineterminator="\n").encode("utf-8"))


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
        zs = load_zones(s["model"]) if s.get("model") else None
        if zs is not None:
            zrows = mrows[mrows["scope"] == "zone"]
            out.append((prefix + "zone_map.png", f"{name}Zone map".strip(),
                        plots.zone_map_figure(zs, sub, zrows, theme, title=f"{name}RMSE by zone".strip())))
            title = f"{name}Error by zone".strip()
            out.append((prefix + "zones.png", title,
                        plots.zones_figure(zs, sub, zrows, res.settings, theme, title=title)))
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
        name = (f"{r['sensor']} / " if r["scope"] == "zone" and r.get("sensor") and r["sensor"] != "" else "") + str(r["name"])
        cls = "" if r.get("enough", True) or r["scope"] != "zone" else ' class="thin"'
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
    for rel, _title, fig in figs:
        p = pdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _png(fig)
        p.write_bytes(data)
        imgs[rel] = data
    (folder / "report.html").write_bytes(_report_html(res, figs, imgs).encode("utf-8"))


def _report_html(res, figs, imgs) -> str:
    r = res.result
    sens = "".join(
        f"<li>{html.escape(s['label'])}: {html.escape(str(s.get('model')))} (channel {s['channel']}, slot {s['slot']}), "
        f"{s['frames']} frames, {s['rate_hz']} Hz, residual lag {html.escape(lag_text(s))}</li>"
        for s in r["sensors"])
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in r["warnings"]) or "<li>None</li>"
    nl = r.get("noload_check") or {}
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
        "<h2>No-load check</h2><p>" + (
            f"Gauge {nl.get('gauge_mean_N')} N, sensor |F| {nl.get('sensor_F_mean_N')} N "
            f"({html.escape(', '.join(nl.get('warnings') or []) or 'no warnings')})" if nl else "Not recorded") + "</p>",
        f"<h2>Warnings</h2><ul>{warn}</ul>",
        "<h2>Metrics (error = |F| − gauge, N; stable samples except the contact columns)</h2>",
        metrics_html(res.metrics),
    ]
    for rel, title, _ in figs:
        parts.append(f"<h2>{html.escape(title)}</h2><img alt='{html.escape(rel)}' "
                     f"src='data:image/png;base64,{base64.b64encode(imgs[rel]).decode('ascii')}'>")
    s = r["settings"]
    parts.append("<h2>Analysis settings</h2><pre>" + html.escape(json.dumps(s, ensure_ascii=False, indent=1)) + "</pre>")
    parts.append("</body></html>\n")
    return "".join(parts)
