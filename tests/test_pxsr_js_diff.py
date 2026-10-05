"""Run the original PXSR JS code and the Python port (codec) on the same inputs and compare results.

Real captures can only check values that actually occurred (e.g. large forces with Z above 127 may be absent).
Here every byte value (0–255) and odd frames are fed in so the PXSR code itself is the reference.
Runs only on a PC with PXSR installed, skipped otherwise.
"""
import random
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pxsr_js  # noqa: E402

from paxkit.device import codec  # noqa: E402

pytestmark = pytest.mark.skipif(not pxsr_js.available(), reason="PXSR is not installed")

FORCES = sorted({t.forces for t in codec.SENSOR_TYPES} | {0, 1, codec.DEFAULT_FORCES})


def _frame(sid, func, addr, body, length=None):
    """Response frame `AA 55 | len | sid 00 | func | addr | ... | cs` (len = total − 5)."""
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
    seqs = []   # each sequence = [(chunk, m), ...], starting from a fresh parser
    # 1) data frames: every byte value appears in X, Y and Z positions
    for m in (31, 52):
        cyc = bytes((i * 7) % 256 for i in range(30 + 3 * m))
        seqs.append([(_data_frame(rng, m, payload=cyc), m)])
        for _ in range(150):
            f = _data_frame(rng, m)
            seqs.append([(c, m) for c in _split(rng, f, rng.randint(1, 4))])
    for m in FORCES:
        seqs.append([(_data_frame(rng, m), m)])
        seqs.append([(_data_frame(rng, m), 52)])     # when the parse taxel count differs from the frame
    # 2) version responses (normal strings + arbitrary bytes, including invalid UTF-8)
    seqs.append([(_frame(3, 0xFB, 6100, [100, 0] + list(b"PAXINI PXSR-STDDP03F-v1.0.5".ljust(100, b"\0"))), 239)])
    for _ in range(100):
        body = bytes(rng.randrange(256) for _ in range(rng.randint(0, 120)))
        seqs.append([(_frame(rng.randrange(9), 0xFB, 6100, [100, 0] + list(body)), 239)])
    # 3) write responses, other function codes and addresses
    for func in (126, 122, 120, 121, 0xFB, 0):
        for addr in (3, 35, 1008, 6100, 0, 12345, -1):
            for st in (0, 1, 255):
                seqs.append([(_frame(3, func, addr, [1, 0, st]), 31)])
    # 4) header error, too short, length mismatch, negative length, two frames at once, continued receive after an unknown address
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
            assert got == r["y"], f"sequence {i}"
            assert p.buf.hex() == r["buf"], f"sequence {i} buffer"
            assert ([warn] if warn else []) == r["warn"], f"sequence {i} warning"


def test_hand_calibration_equal_js():
    """HAND `setHandCalibration` and checksum `$9` (reader after P1b; compare the command only for now)."""
    rng = random.Random(20261005)
    arrays = [[]] + [[rng.randrange(256) for _ in range(rng.randint(1, 40))] for _ in range(300)]
    harness = r"""
    const h = ka0();
    OUTPUT = {cal: Buffer.from(h.setHandCalibration()).toString('hex'), sums: INPUT.arrays.map(a => $9(a))};
    """
    out = pxsr_js.run(harness, {"arrays": arrays}, prelude=pxsr_js.hand_source())
    assert codec.hand_set_calibration().hex() == out["cal"]
    assert [codec.hand_checksum(a) for a in arrays] == out["sums"]
