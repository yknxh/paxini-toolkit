"""PXSR 원본 JS 코드와 Python 이식본(codec)을 같은 입력으로 돌려 결과를 비교한다.

실기 캡처는 실제로 나온 값만 확인할 수 있다 (예: Z가 127을 넘는 큰 힘은 캡처에 없을 수 있음).
여기서는 모든 바이트 값(0~255)과 이상한 프레임까지 넣어 PXSR 코드 자체를 기준으로 삼는다.
PXSR이 설치된 PC에서만 실행되고, 없으면 건너뛴다.
"""
import random
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pxsr_js  # noqa: E402

from paxkit.device import codec  # noqa: E402

pytestmark = pytest.mark.skipif(not pxsr_js.available(), reason="PXSR이 설치되어 있지 않음")

FORCES = sorted({t.forces for t in codec.SENSOR_TYPES} | {0, 1, codec.DEFAULT_FORCES})


def _frame(sid, func, addr, body, length=None):
    """응답 프레임 `AA 55 | len | sid 00 | func | addr | ... | cs` (len = 전체 − 5)."""
    head = [0xAA, 0x55, 0, 0, sid, 0, func, *addr.to_bytes(4, "little", signed=True)]
    f = head + list(body)
    n = len(f) + 1 - 5 if length is None else length
    f[2:4] = list((n & 0xFFFF).to_bytes(2, "little"))
    f.append(codec.checksum(f))
    return bytes(f)


def _data_frame(rng, m, sid=3, payload=None):
    if payload is None:
        payload = bytes(rng.randrange(256) for _ in range(30 + 3 * m))
    return _frame(sid, 0xFB, 1008, list((30 + 3 * m).to_bytes(2, "little")) + list(payload))


def _split(rng, data, parts):
    cuts = sorted(rng.sample(range(1, len(data)), min(parts - 1, len(data) - 1))) if parts > 1 else []
    return [data[a:b] for a, b in zip([0] + cuts, cuts + [len(data)])]


def _sequences():
    rng = random.Random(20261004)
    seqs = []   # 각 시퀀스 = [(chunk, m), ...], 새 파서에서 시작
    # 1) 데이터 프레임: 모든 바이트 값이 X·Y·Z 자리에 다 나오게
    for m in (31, 52):
        cyc = bytes((i * 7) % 256 for i in range(30 + 3 * m))
        seqs.append([(_data_frame(rng, m, payload=cyc), m)])
        for _ in range(150):
            f = _data_frame(rng, m)
            seqs.append([(c, m) for c in _split(rng, f, rng.randint(1, 4))])
    for m in FORCES:
        seqs.append([(_data_frame(rng, m), m)])
        seqs.append([(_data_frame(rng, m), 52)])     # 파싱 taxel 수가 프레임과 다를 때
    # 2) 버전 응답 (정상 문자열 + 아무 바이트, 잘못된 UTF-8 포함)
    seqs.append([(_frame(3, 0xFB, 6100, [100, 0] + list(b"PAXINI PXSR-STDDP03F-v1.0.5".ljust(100, b"\0"))), 239)])
    for _ in range(100):
        body = bytes(rng.randrange(256) for _ in range(rng.randint(0, 120)))
        seqs.append([(_frame(rng.randrange(9), 0xFB, 6100, [100, 0] + list(body)), 239)])
    # 3) 쓰기 응답·기타 기능 코드·주소
    for func in (126, 122, 120, 121, 0xFB, 0):
        for addr in (3, 35, 1008, 6100, 0, 12345, -1):
            for st in (0, 1, 255):
                seqs.append([(_frame(3, func, addr, [1, 0, st]), 31)])
    # 4) 헤더 오류, 너무 짧음, 길이 불일치, 음수 길이, 두 프레임이 한 번에 옴, 알 수 없는 주소 뒤 이어 받기
    good = _data_frame(rng, 31)
    seqs.append([(b"\x00" + good[1:], 31)])
    seqs.append([(good[:13], 31), (good[13:], 31)])
    seqs.append([(good + good, 31)])
    seqs.append([(good[:-1], 31), (good[-1:] + good, 31)])
    seqs.append([(_frame(3, 0xFB, 1008, [1, 0, 5], length=0x8000 + 3), 31)])
    seqs.append([(_frame(3, 0xFB, 777, [1, 0, 5]), 31), (good, 31)])
    seqs.append([(bytes(rng.randrange(256) for _ in range(rng.randint(1, 300))), 31) for _ in range(50)])
    return seqs


HARNESS = r"""
const hex = b => Buffer.from(b).toString('hex');
OUTPUT = {cmds: [], seqs: []};
const c = Na0();
for (const sid of INPUT.sids) {
  c.setSericeID(sid);
  OUTPUT.cmds.push([hex(c.getVersion()), INPUT.forces.map(m => hex(c.getTypeData(m))), hex(c.setCalibration())]);
}
for (const seq of INPUT.seqs) {
  const u = Na0();
  const res = [];
  for (const [h, m] of seq) {
    const y = await u.parseUsbData(Buffer.from(h, 'hex'), m);
    res.push({y, buf: u.usbDataView.value.toString('hex'), warn: __warn.splice(0)});
  }
  OUTPUT.seqs.push(res);
}
"""


@pytest.fixture(scope="module")
def js_result():
    seqs = _sequences()
    payload = {"sids": list(range(256)), "forces": FORCES,
               "seqs": [[[c.hex(), m] for c, m in s] for s in seqs]}
    return seqs, pxsr_js.run(HARNESS, payload)


def test_commands_equal_js(js_result):
    _, out = js_result
    for sid, (ver, data, cal) in zip(range(256), out["cmds"]):
        assert codec.usb_get_version(sid).hex() == ver
        assert [codec.usb_get_type_data(sid, m).hex() for m in FORCES] == data
        assert codec.usb_set_calibration(sid).hex() == cal


def test_usb_parser_equal_js(js_result):
    seqs, out = js_result
    assert len(seqs) == len(out["seqs"])
    for i, (seq, res) in enumerate(zip(seqs, out["seqs"])):
        p = codec.UsbParser()
        for (chunk, m), r in zip(seq, res):
            y = p.feed(chunk, m)
            got = asdict(y)
            warn = got.pop("warning")
            assert got == r["y"], f"시퀀스 {i}"
            assert p.buf.hex() == r["buf"], f"시퀀스 {i} 버퍼"
            assert ([warn] if warn else []) == r["warn"], f"시퀀스 {i} 경고"
