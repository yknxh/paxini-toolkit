"""Capture replay tool (shared by tests): returns sensor responses in captured PXSR command order with the captured delays."""
import gzip
import json
from pathlib import Path

from paxkit.device import codec
from paxkit.device.clock import VirtualClock
from paxkit.device.usb import UsbSensor

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(p for p in FIXTURES.glob("usb_*") if (p / "serial.jsonl.gz").is_file())


class ReplayTransport:
    def __init__(self, rows, clock):
        self.clock = clock
        self.steps = []   # [tx, tx time, [(delay after tx, rx chunk)]]
        for r in rows:
            if r["dir"] == "tx":
                self.steps.append([bytes.fromhex(r["hex"]), float(r["t"]), []])
            elif r["dir"] == "rx" and self.steps:
                self.steps[-1][2].append((float(r["t"]) - self.steps[-1][1], bytes.fromhex(r["hex"])))
        self.written = []    # [(time, bytes)]
        self.rx_done = []    # time the last response chunk of command k was delivered
        self._pending = []
        self.on_tx = None
        self.is_open = False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def write(self, data):
        now = self.clock.now()
        i = len(self.written)
        self.written.append((now, bytes(data)))
        if i < len(self.steps) and bytes(data) == self.steps[i][0]:
            rx = self.steps[i][2]
            for k, (d, chunk) in enumerate(rx):
                self._pending.append((now + d, chunk, i if k == len(rx) - 1 else None))
        if self.on_tx:
            self.on_tx(i)

    def read_available(self):
        now = self.clock.now()
        due = [p for p in self._pending if p[0] <= now]
        self._pending = [p for p in self._pending if p[0] > now]
        for _, _, last_of in due:
            if last_of is not None:
                self.rx_done.append((last_of, now))
        return b"".join(c for _, c, _ in due)


def load_capture(case):
    with gzip.open(case / "serial.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def run_replay(case, setup=None):
    """Replay one capture with UsbSensor. setup(sensor) can attach extra sinks."""
    rows = load_capture(case)
    clock = VirtualClock(wall_base=float(rows[0]["t"]))
    tr = ReplayTransport(rows, clock)
    # PXSR's configured sensor type during capture was S1813E (the B sensor switches via the version response)
    s = UsbSensor(tr, clock=clock, specification="S1813E")
    frames, calls = [], {}
    s.add_sink(frames.append)
    if setup is not None:
        setup(s)
    cal_idx = {i for i, st in enumerate(tr.steps) if st[0][6] == codec.USB_FUNC_WRITE}

    def on_tx(i):
        if i + 1 in cal_idx:            # replayed as if the user pressed calibration right after this request
            calls[i + 1] = clock.now()
            s.calibrate()
        if i == len(tr.steps):          # end of capture: one more unanswered request goes out, then disconnect
            s.disconnect()

    tr.on_tx = on_tx
    s.run()   # virtual clock, so run directly on the current thread
    return tr, s, frames, calls
