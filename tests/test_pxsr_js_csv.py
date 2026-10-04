"""PXSR이 쓰는 `csv-writer`(설치본 node_modules)와 파일명 함수 `rs0`(번들)를 직접 돌려
Python 이식본(`paxkit.recording.pxsr_csv`)과 결과 파일 바이트를 비교한다.

실제 PXSR CSV에는 정수와 시각 문자열만 나오지만, 여기서는 따옴표·쉼표·줄바꿈·빈 값·undefined·한글 등
이상한 값까지 넣어 csv-writer 자체를 기준으로 삼는다. PXSR이 설치된 PC에서만 실행되고, 없으면 건너뛴다.
"""
import random
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pxsr_js  # noqa: E402

from paxkit.recording import pxsr_csv  # noqa: E402

pytestmark = pytest.mark.skipif(not pxsr_js.available(), reason="PXSR이 설치되어 있지 않음")

UNDEFINED = {"u": 1}   # JSON에 undefined가 없어 표시로 넘긴다

HARNESS = r"""
const path = require('path');
const undef = v => (v && typeof v === 'object' && v.u) ? undefined : v;
const files = [];
for (const c of INPUT.cases) {
  const p = path.join(INPUT.dir, c.name);
  // cs0: createArrayCsvWriter({path, ...this.header.length&&{header:this.header}})
  const w = csvWriter.createArrayCsvWriter({path: p, ...(c.header && c.header.length && {header: c.header.map(undef)})});
  for (const b of c.batches) await w.writeRecords(b.map(r => r.map(undef)));
  files.push(fs.readFileSync(p).toString('hex'));
}
const names = INPUT.dates.map(d => rs0(new Date(d[0], d[1] - 1, d[2], d[3], d[4], d[5]), d[6]));
OUTPUT = {files, names};
"""

FORMATS = ["YYYY-MM-dd-HHmmSS", "Y-M-d-H-m-s", "yy/MM/DD hh:mm:ss", "YY.M.d HHmmSS", "SSSS HHH mmm", "dd-YYYY"]


def _value(rng):
    k = rng.randrange(8)
    if k == 0:
        return rng.randint(-300, 300)
    if k == 1:
        return rng.choice([0, -1, 255, 2 ** 31, -(2 ** 53) + 1, 2 ** 53 - 1])
    if k == 2:
        return rng.choice([True, False])
    if k == 3:
        return None
    if k == 4:
        return UNDEFINED
    return "".join(rng.choice(['a', '1', ',', '"', '\n', '\r', ' ', '-', ':', '한', '😀', '.'])
                   for _ in range(rng.randint(0, 6)))


def _cases():
    rng = random.Random(20261005)
    cases = []
    for i in range(60):
        width = rng.randint(1, 12)
        hk = i % 4
        header = None if hk == 0 else [] if hk == 1 else [_value(rng) for _ in range(width)] if hk == 2 \
            else ["Timestamp"] + [f"0-2-NxN-X[{j}]" for j in range(width - 1)]
        batches = [[[_value(rng) for _ in range(width if rng.random() < 0.9 else rng.randint(1, 12))]
                    for _ in range(rng.randint(1, 5))] for _ in range(rng.randint(1, 4))]
        cases.append({"name": f"case{i}.csv", "header": header, "batches": batches})
    dates = []
    for _ in range(200):
        d = datetime(rng.randint(1990, 2099), rng.randint(1, 12), rng.randint(1, 28),
                     rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
        dates.append([d.year, d.month, d.day, d.hour, d.minute, d.second, rng.choice(FORMATS)])
    return cases, dates


@pytest.fixture(scope="module")
def js_result(tmp_path_factory):
    cases, dates = _cases()
    d = tmp_path_factory.mktemp("js")
    out = pxsr_js.run(HARNESS, {"cases": cases, "dates": dates, "dir": str(d)}, prelude=pxsr_js.csv_source())
    return cases, dates, out


def _py(v):
    return None if v == UNDEFINED else v


def test_csv_writer_equal_js(js_result, tmp_path):
    cases, _, out = js_result
    for c, js_hex in zip(cases, out["files"]):
        header = [_py(v) for v in c["header"]] if c["header"] else c["header"]
        w = pxsr_csv.CsvFileWriter(tmp_path / c["name"], header)
        for b in c["batches"]:
            w.write_records([[_py(v) for v in r] for r in b])
        assert (tmp_path / c["name"]).read_bytes() == bytes.fromhex(js_hex), c["name"]


def test_rs0_equal_js(js_result):
    _, dates, out = js_result
    for d, js_name in zip(dates, out["names"]):
        assert pxsr_csv.rs0(datetime(*d[:6]), d[6]) == js_name, d
