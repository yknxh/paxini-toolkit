"""(Re-)analyzes gauge test session folders (plan P6-2). Same code as the GUI "Re-analyze" button.

    python tools/bench_analyze.py <session folder>... [--set name=value ...]
    python tools/bench_analyze.py --latest

Settings default to the values saved in the session's `meta.json`. Values changed with --set are kept in result.json.
Results (samples.csv, metrics.csv, result.json, plots/, report.html) overwrite those in the session folder.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.bench import analyze_session  # noqa: E402
from paxkit.paths import data_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", type=Path)
    ap.add_argument("--latest", action="store_true", help="most recent session in data/bench/")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    a = ap.parse_args()
    folders = list(a.folders)
    if a.latest:
        cands = sorted(p for p in data_path("bench").iterdir() if (p / "meta.json").is_file())
        folders += cands[-1:]
    if not folders:
        ap.error("give session folders or --latest")
    override = {}
    for kv in a.set:
        k, _, v = kv.partition("=")
        override[k.strip()] = yaml.safe_load(v)
    for f in folders:
        res = analyze_session(f, override or None)
        m = res.metrics.iloc[0]
        print(f"{f.name}: stable samples {int(m['n'])}, bias {m['bias_N']}, RMSE {m['rmse_N']} N, "
              f"slope {m['slope']}, R² {m['r2']}; contact samples {int(m['n_contact'])}, bias {m['bias_contact_N']}, "
              f"RMSE {m['rmse_contact_N']} N")
        for w in res.warnings:
            print(f"  warning: {w}")
        print(f"  → {f / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
