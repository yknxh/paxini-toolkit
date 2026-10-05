"""세션 폴더 분석 (계획 P6-2): 기록 파일(센서 CSV + `gauge.csv` + `meta.json`)만 읽어 결과를 만든다.

기록 직후 분석과 나중 재분석(GUI 버튼, `tools/bench_analyze.py`)이 같은 코드·같은 결과.
설정은 기본으로 `meta.json`에 복사해 둔 값을 쓰고, 바꿔서 재분석하면 쓴 값이 `result.json`에 남는다.

게이지 지연: 게이지 리더가 수신 시각에서 고정 지연을 이미 뺀다 (`gauge/reader.py`). 분석에서는 세션마다 남은 지연을
상호상관으로 재서 (`lag_s`), 상관이 `lag_min_r` 이상이면 게이지 시각을 그만큼 옮긴 뒤 짝짓는다 (`lag_correct`, 2026-10-05 사용자 요청).
옮긴 값은 `result.json` 센서 정보의 `lag_applied_s`. `gauge.csv`는 바꾸지 않는다.

센서가 여럿(HAND)이면 게이지 샘플마다 |F|가 가장 큰 센서를 "눌린 센서"로 보고, 두 번째 센서가
첫 번째의 `simultaneous_ratio` 이상이면 동시 접촉으로 보고 지표에서 뺀다. 간섭·채널 대응도 여기서 계산한다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from .. import __version__
from ..device.geometry import load_geometry
from ..gauge.sync import xcorr_offset
from ..recording.reader import read_log
from .metrics import COLUMNS as METRIC_COLUMNS, _r, group_metrics
from .pairing import SensorSeries, pair_sensor
from .session import GAUGE_FILE, META_FILE
from .settings import bench_settings
from .zones import NO_ZONE, load_zones

RESULT_FORMAT = "paxkit-bench-result/1"
ANALYSIS_VERSION = 1
SAMPLE_COLUMNS = ["t_unix_s", "sensor", "gauge_N", "F_N", "Fx_N", "Fy_N", "Fz_N", "error_N", "error_z_N",
                  "contact", "stable", "noload", "simultaneous", "zone", "cop_x_mm", "cop_y_mm", "cop_z_mm"]
LAG_MAX_S = 0.5
LAG_MIN_STD_N = 0.3


@dataclass
class BenchResult:
    folder: Path
    samples: pd.DataFrame
    metrics: pd.DataFrame
    crosstalk: Optional[pd.DataFrame]
    result: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    @property
    def settings(self) -> Dict[str, Any]:
        return self.result["settings"]

    @property
    def sensors(self) -> List[Dict[str, Any]]:
        return self.result["sensors"]

    def sensor_samples(self, label: str) -> pd.DataFrame:
        return self.samples[self.samples["sensor"] == label]

    def sensor_metrics(self, label: str) -> pd.DataFrame:
        return self.metrics[self.metrics["sensor"] == label]


def read_meta(folder: Path) -> Dict[str, Any]:
    p = folder / META_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def find_sensor_csv(folder: Path, meta: Dict[str, Any]) -> Optional[Path]:
    name = meta.get("sensor_csv")
    if name and (folder / name).is_file():
        return folder / name
    cands = sorted(p for p in folder.glob("????-??-??-??????.csv"))
    return cands[0] if cands else None


def read_gauge(folder: Path):
    p = folder / GAUGE_FILE
    if not p.is_file():
        return np.zeros(0), np.zeros(0)
    d = pd.read_csv(p)
    return d["t_unix_s"].to_numpy(dtype=float), d["force_N"].to_numpy(dtype=float)


def _model_for(n_taxels: int, meta_model: Optional[str]) -> Optional[str]:
    if meta_model and load_geometry(meta_model) is not None:
        return meta_model
    for m in ("S1813E", "S2015"):
        g = load_geometry(m)
        if g is not None and g.n_taxels == n_taxels:
            return m
    return meta_model


def _sensor_lag(gt, gv, st, smag):
    """게이지 N ↔ 센서 |F| 상호상관 지연. lag > 0 이면 센서가 늦다. 움직임이 없으면 None."""
    if len(gt) < 20 or len(st) < 20 or np.nanstd(gv) < LAG_MIN_STD_N:
        return None, None
    ok = np.isfinite(smag)
    lo = max(gt[0], st[ok][0]) + LAG_MAX_S
    hi = min(gt[-1], st[ok][-1]) - LAG_MAX_S
    lag, r = xcorr_offset(gt, gv, st[ok], smag[ok], lo, hi, LAG_MAX_S)
    return (None, None) if lag is None else (round(lag, 4), round(r, 4))


def analyze_session(folder: Union[str, Path], settings: Optional[Dict[str, Any]] = None, *,
                    write: bool = True) -> BenchResult:
    folder = Path(folder)
    meta = read_meta(folder)
    s = bench_settings(override=meta.get("settings") or None)
    if settings:
        s.update(settings)
    warnings: List[str] = []

    gt, gv = read_gauge(folder)
    bad = ~np.isfinite(gv) | (np.abs(gv) > float(s["gauge_max_N"]))
    if bad.any():
        warnings.append(f"게이지 이상값 {int(bad.sum())}개 제외 (|값| > {s['gauge_max_N']:g} N)")
    gt, gv = gt[~bad], gv[~bad]
    order = np.argsort(gt, kind="stable")
    gt, gv = gt[order], gv[order]

    csv = find_sensor_csv(folder, meta)
    if csv is None:
        raise FileNotFoundError(f"센서 기록 CSV가 없음: {folder}")
    log = read_log(csv)
    meta_sensors = {(m.get("channel"), m.get("slot")): m for m in meta.get("sensors", [])}
    sensors: List[Dict[str, Any]] = []
    per: List[Dict[str, np.ndarray]] = []
    for key in sorted(log.sensors):
        ms = meta_sensors.get(key, {})
        tz = log.taxel_raw(key, "Z")
        model = _model_for(tz.shape[1], ms.get("sensor"))
        label = ms.get("label") or (meta.get("label") if len(log.sensors) == 1 else None) or f"{key[0]}-{key[1]}"
        fr = log.force_raw(key)
        series = SensorSeries(log.t, fr, tz)
        zs = load_zones(model) if model else None
        if zs is None:
            warnings.append(f"{label}: 구역 정의 없음 ({model}) → 구역별 결과 없음")
        smag = np.sqrt(((fr * 0.1) ** 2).sum(axis=1))
        lag, r = _sensor_lag(gt, gv, log.t, smag)
        # sensor(t + lag) ≈ gauge(t) → 게이지 샘플 시각을 lag만큼 옮기면 같은 순간끼리 짝지어진다
        shift = lag if (s["lag_correct"] and lag is not None and r is not None and r >= float(s["lag_min_r"])) else 0.0
        cols = pair_sensor(gt + shift, gv, series, s, zs)
        dur = float(log.t[-1] - log.t[0]) if len(log.t) > 1 else 0.0
        info = {"label": label, "channel": key[0], "slot": key[1], "model": model, "taxels": int(tz.shape[1]),
                "frames": int(len(log.t)), "rate_hz": _r(len(log.t) / dur if dur > 0 else 0.0, 2),
                "lag_s": lag, "lag_r": r, "lag_applied_s": round(shift, 4), "zones": [z.id for z in zs.zones] if zs else [],
                "zone_names": [z.name for z in zs.zones] if zs else []}
        if lag is not None and abs(lag) > float(s["lag_warn_s"]):
            how = "분석에서 보정함" if shift else f"보정 안 함 (상관 r {r:.2f} < {float(s['lag_min_r']):g} 또는 lag_correct 꺼짐)"
            warnings.append(f"{label}: 센서-게이지 지연 {lag * 1e3:+.0f} ms (|지연| > {s['lag_warn_s'] * 1e3:.0f} ms, {how}"
                            " — 게이지 latency_s 설정을 다시 재 보세요)")
        sensors.append(info)
        per.append(cols)

    n = len(gt)
    if not per:
        raise ValueError("센서 열이 없음")
    # 눌린 센서 (HAND) — 센서 1개면 항상 그 센서
    mags = np.vstack([np.where(np.isfinite(c["F_N"]), c["F_N"], -np.inf) for c in per])
    pressed = mags.argmax(axis=0)
    simultaneous = np.zeros(n, dtype=bool)
    if len(per) > 1:
        srt = np.sort(mags, axis=0)
        first, second = srt[-1], srt[-2]
        simultaneous = (first > 0) & (second >= float(s["simultaneous_ratio"]) * first) & (gv >= float(s["contact_N"]))
        if simultaneous.any():
            warnings.append(f"동시 접촉 {int(simultaneous.sum())}개 제외 (두 번째 센서 ≥ 첫 번째의 {s['simultaneous_ratio']:g}배)")
    idx = np.arange(n)
    cols: Dict[str, np.ndarray] = {}
    for k in per[0]:
        stack = np.vstack([c[k] for c in per]) if per[0][k].ndim == 1 else None
        cols[k] = stack[pressed, idx] if stack is not None else per[0][k]
    for k in ("contact", "stable"):
        cols[k] = cols[k] & ~simultaneous
    cols["simultaneous"] = simultaneous
    labels = np.array([x["label"] for x in sensors])
    zone_ids = [x["zones"] for x in sensors]
    zone_col = np.array([zone_ids[p][z] if z != NO_ZONE else "" for p, z in zip(pressed, cols["zone"])], dtype=object)
    sensor_col = labels[pressed] if n else np.array([], dtype=object)

    # 지표
    rows: List[Dict[str, Any]] = []
    all_mask = np.ones(n, dtype=bool)
    noload = cols["noload"]
    rows.append({"scope": "overall", "sensor": "" if len(per) > 1 else sensors[0]["label"], "id": "all",
                 "name": "전체", **group_metrics(cols, all_mask, s, noload)})
    for i, info in enumerate(sensors):
        sm = sensor_col == info["label"]
        if len(per) > 1:
            # 무부하 잔류는 센서마다 그 센서의 |F| (눌린 센서와 무관)
            own = dict(cols, F_N=per[i]["F_N"])
            nl = group_metrics(own, np.zeros(n, dtype=bool), s, noload)
            m = group_metrics(cols, sm, s, None)
            m.update({k: nl[k] for k in ("n_noload", "noload_mean_N", "noload_max_N")})
            rows.append({"scope": "sensor", "sensor": info["label"], "id": "all", "name": info["label"], **m})
        for zk, (zid, zname) in enumerate(zip(info["zones"], info["zone_names"])):
            zm = sm & (cols["zone"] == zk)
            rows.append({"scope": "zone", "sensor": info["label"], "id": zid, "name": zname,
                         **group_metrics(cols, zm, s, None)})
        nz = sm & cols["stable"] & (cols["zone"] == NO_ZONE)
        if nz.any():
            warnings.append(f"{info['label']}: 위치 없음(taxel Z 합 < {s['min_taxel_sum']:g}) 안정 샘플 {int(nz.sum())}개 — 전체에만 포함")
    metrics = pd.DataFrame(rows, columns=["scope", "sensor", "id", "name"] + METRIC_COLUMNS)

    crosstalk = _crosstalk(per, pressed, cols, gv, labels) if len(per) > 1 else None
    channel_check = _channel_check(meta, sensor_col, cols, warnings) if len(per) > 1 else []

    samples = pd.DataFrame({
        "t_unix_s": cols["t_unix_s"], "sensor": sensor_col, "gauge_N": cols["gauge_N"], "F_N": cols["F_N"],
        "Fx_N": cols["Fx_N"], "Fy_N": cols["Fy_N"], "Fz_N": cols["Fz_N"], "error_N": cols["error_N"],
        "error_z_N": cols["error_z_N"], "contact": cols["contact"].astype(int), "stable": cols["stable"].astype(int),
        "noload": cols["noload"].astype(int), "simultaneous": simultaneous.astype(int), "zone": zone_col,
        "cop_x_mm": cols["cop_x_mm"], "cop_y_mm": cols["cop_y_mm"], "cop_z_mm": cols["cop_z_mm"],
    }, columns=SAMPLE_COLUMNS)
    samples = samples[np.isfinite(cols["F_N"])].reset_index(drop=True)
    unpaired = int(n - len(samples))
    if unpaired:
        warnings.append(f"센서 짝이 없는 게이지 샘플 {unpaired}개 제외 (센서 앞뒤 프레임 간격 > {s['max_gap_s']:g} s 또는 기록 범위 밖)")
    st = metrics[metrics["scope"] == "zone"]
    thin = st[~st["enough"].astype(bool)]
    if len(thin):
        warnings.append(f"데이터 부족 구역 {len(thin)}개 (안정 샘플 < {s['zone_min_samples']}): " + ", ".join(
            (f"{a}/" if len(per) > 1 else "") + b for a, b in zip(thin["sensor"], thin["name"])))

    noload_events = [e for e in meta.get("events", []) if e.get("kind") == "noload_check"]
    dur = float(gt[-1] - gt[0]) if n > 1 else 0.0
    result = {
        "format": RESULT_FORMAT,
        "analysis_version": ANALYSIS_VERSION,
        "paxkit": __version__,
        "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "folder": folder.name,
        "label": meta.get("label", ""),
        "model": meta.get("model", ""),
        "sensor_csv": csv.name,
        "settings": s,
        "sensors": sensors,
        "gauge": {"rows": int(len(gt) + bad.sum()), "outliers": int(bad.sum()),
                  "rate_hz": _r(n / dur if dur > 0 else 0.0, 2)},
        "counts": {"gauge_samples": n, "paired": int(len(samples)), "contact": int(cols["contact"].sum()),
                   "stable": int(cols["stable"].sum()), "noload": int(cols["noload"].sum()),
                   "simultaneous": int(simultaneous.sum())},
        "duration_s": _r(dur, 3),
        "noload_check": noload_events[-1] if noload_events else None,
        "metrics": _records(metrics),
        "crosstalk": _records(crosstalk) if crosstalk is not None else None,
        "channel_check": channel_check,
        "warnings": warnings,
    }
    res = BenchResult(folder, samples, metrics, crosstalk, result, warnings)
    if write:
        from .report import write_outputs
        write_outputs(res)
    return res


def _records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for row in df.to_dict(orient="records"):
        out.append({k: (None if isinstance(v, float) and not np.isfinite(v) else
                        (v.item() if isinstance(v, np.generic) else v)) for k, v in row.items()})
    return out


def _crosstalk(per, pressed, cols, gv, labels) -> pd.DataFrame:
    """센서 i를 누르는 동안(접촉·동시 아님, 게이지 ≥ 1 N) 다른 센서 j의 |F|."""
    rows = []
    base = cols["contact"] & (gv >= 1.0)
    for i, li in enumerate(labels):
        sel = base & (pressed == i)
        for j, lj in enumerate(labels):
            if i == j:
                continue
            fj = per[j]["F_N"][sel]
            fi = per[i]["F_N"][sel]
            ok = np.isfinite(fj) & np.isfinite(fi) & (fi > 0)
            fj, fi = fj[ok], fi[ok]
            rows.append({"pressed": li, "other": lj, "n": int(len(fj)),
                         "max_N": _r(fj.max()) if len(fj) else None,
                         "p95_N": _r(np.percentile(fj, 95)) if len(fj) else None,
                         "p95_ratio_pct": _r(100 * np.percentile(fj / fi, 95)) if len(fj) else None})
    return pd.DataFrame(rows, columns=["pressed", "other", "n", "max_N", "p95_N", "p95_ratio_pct"])


def _channel_check(meta, sensor_col, cols, warnings) -> List[Dict[str, Any]]:
    """"다음 센서" 이벤트로 나뉜 구간마다 실제로 가장 많이 눌린 센서가 안내한 센서와 같은지."""
    ev = sorted((e for e in meta.get("events", []) if e.get("kind") == "sensor_switch"),
                key=lambda e: e.get("t_unix_s", 0))
    out = []
    t = cols["t_unix_s"]
    for k, e in enumerate(ev):
        t0 = e.get("t_unix_s", 0)
        t1 = ev[k + 1].get("t_unix_s", np.inf) if k + 1 < len(ev) else np.inf
        sel = (t >= t0) & (t < t1) & cols["contact"]
        labs, cnt = np.unique(sensor_col[sel], return_counts=True)
        got = str(labs[cnt.argmax()]) if len(labs) else None
        ok = got == e.get("label")
        out.append({"guided": e.get("label"), "pressed": got, "samples": int(sel.sum()), "match": bool(ok)})
        if got is not None and not ok:
            warnings.append(f"채널 대응: '{e.get('label')}' 차례에 '{got}'이(가) 눌림 (배선·설정 확인)")
    return out


def load_result(folder: Union[str, Path]) -> Optional[BenchResult]:
    """저장된 결과(`result.json`, `samples.csv`, `metrics.csv`)를 다시 읽는다 (재분석 없이 GUI 표시용)."""
    folder = Path(folder)
    p = folder / "result.json"
    if not p.is_file() or not (folder / "samples.csv").is_file():
        return None
    result = json.loads(p.read_text(encoding="utf-8"))
    samples = pd.read_csv(folder / "samples.csv", dtype={"sensor": str, "zone": str}, keep_default_na=False,
                          na_values=[""])
    samples["sensor"] = samples["sensor"].fillna("").astype(str)
    samples["zone"] = samples["zone"].fillna("").astype(str)
    metrics = pd.DataFrame(result["metrics"])
    ct = pd.DataFrame(result["crosstalk"]) if result.get("crosstalk") else None
    return BenchResult(folder, samples, metrics, ct, result, list(result.get("warnings", [])))
