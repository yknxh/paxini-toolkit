"""분석 결과 파일 (계획 4.2): samples.csv, metrics.csv, crosstalk.csv(HAND), result.json, plots/*.png, report.html.

재분석하면 덮어쓴다. 같은 폴더를 두 번 분석하면 `analyzed_at`을 뺀 내용이 같다 (값 반올림·고정 열 순서).
그림은 밝은 테마로 저장한다 (GUI는 같은 그림을 어두운 테마로 다시 그린다).
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
    ("name", "구역"), ("n", "n"), ("gauge_min_N", "게이지 최소"), ("gauge_max_N", "게이지 최대"),
    ("bias_N", "bias"), ("sd_N", "SD"), ("rmse_N", "RMSE"), ("mae_N", "MAE"), ("p95_abs_N", "P95 |e|"),
    ("max_abs_N", "최대 |e|"), ("rmse_pct_fs", "RMSE %FS"), ("slope", "기울기"), ("intercept", "절편"), ("r2", "R²"),
    ("n_contact", "접촉 n"), ("rmse_contact_N", "접촉 RMSE"), ("noload_mean_N", "무부하 평균"), ("noload_max_N", "무부하 최대"),
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
    """(파일 경로(plots/ 기준), 제목, Figure) 목록. GUI도 이 목록을 쓴다."""
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
        out.append((prefix + "overall_error.png", f"{name}전체 오차",
                    plots.overall_error_figure(sub, row, res.result, s, theme, title=f"{name}전체 오차".strip())))
        zs = load_zones(s["model"]) if s.get("model") else None
        if zs is not None:
            zrows = mrows[mrows["scope"] == "zone"]
            out.append((prefix + "zone_map.png", f"{name}구역 지도",
                        plots.zone_map_figure(zs, sub, zrows, theme, title=f"{name}구역별 RMSE".strip())))
            out.append((prefix + "zones.png", f"{name}구역별 오차",
                        plots.zones_figure(zs, sub, zrows, res.settings, theme, title=f"{name}구역별 오차".strip())))
    if multi:
        srows = res.metrics[res.metrics["scope"] == "sensor"]
        out.insert(0, ("overall_error.png", "전체 오차 (모든 센서)",
                       plots.overall_error_figure(res.samples, res.metrics.iloc[0].to_dict(), res.result, None, theme,
                                                  title="전체 오차 (모든 센서)")))
        out.append(("sensors_compare.png", "센서 비교", plots.sensors_compare_figure(srows, theme)))
        if res.crosstalk is not None:
            out.append(("crosstalk.png", "간섭", plots.crosstalk_figure(res.crosstalk, [s["label"] for s in res.sensors], theme)))
    return out


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=fig.get_facecolor())
    return buf.getvalue()


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    if isinstance(v, (bool, np.bool_)):
        return "충분" if v else "부족"
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
        f"<li>{html.escape(s['label'])}: {html.escape(str(s.get('model')))} (채널 {s['channel']}, 슬롯 {s['slot']}), "
        f"프레임 {s['frames']}, {s['rate_hz']} Hz, 남은 지연 {html.escape(lag_text(s))}</li>"
        for s in r["sensors"])
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in r["warnings"]) or "<li>없음</li>"
    nl = r.get("noload_check") or {}
    c = r["counts"]
    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        f"<title>{html.escape(r['folder'])}</title><style>",
        "body{font-family:sans-serif;margin:24px;color:#222} table{border-collapse:collapse;font-size:12px}"
        "td,th{border:1px solid #ccc;padding:3px 6px;text-align:right} th{background:#f2f2f2}"
        "td:first-child{text-align:left} tr.thin td{color:#999} img{max-width:100%;border:1px solid #eee;margin:8px 0}"
        "</style></head><body>",
        f"<h1>게이지 테스트 결과 — {html.escape(r['folder'])}</h1>",
        f"<p>라벨 {html.escape(str(r.get('label')))}, 모델 {html.escape(str(r.get('model')))}, 기록 {r.get('duration_s')} s, "
        f"분석 {html.escape(r['analyzed_at'])} (paxkit {html.escape(r['paxkit'])}). 합격/불합격 판정은 하지 않습니다.</p>",
        f"<p>게이지 샘플 {c['gauge_samples']} (이상값 제외 {r['gauge']['outliers']}), 짝지음 {c['paired']}, "
        f"접촉 {c['contact']}, 안정 {c['stable']}, 무부하 {c['noload']}"
        + (f", 동시 접촉 제외 {c['simultaneous']}" if c.get("simultaneous") else "") + "</p>",
        f"<h2>센서</h2><ul>{sens}</ul>",
        "<h2>무부하 확인</h2><p>" + (
            f"게이지 {nl.get('gauge_mean_N')} N, 센서 |F| {nl.get('sensor_F_mean_N')} N "
            f"({html.escape(', '.join(nl.get('warnings') or []) or '경고 없음')})" if nl else "기록 없음") + "</p>",
        f"<h2>경고</h2><ul>{warn}</ul>",
        "<h2>지표 (안정 샘플, 오차 = |F| − 게이지, N)</h2>", metrics_html(res.metrics),
    ]
    for rel, title, _ in figs:
        parts.append(f"<h2>{html.escape(title)}</h2><img alt='{html.escape(rel)}' "
                     f"src='data:image/png;base64,{base64.b64encode(imgs[rel]).decode('ascii')}'>")
    s = r["settings"]
    parts.append("<h2>분석 설정</h2><pre>" + html.escape(json.dumps(s, ensure_ascii=False, indent=1)) + "</pre>")
    parts.append("</body></html>\n")
    return "".join(parts)
