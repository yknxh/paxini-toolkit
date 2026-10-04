"""캡처 재생 도구 (테스트 공용): 캡처한 PXSR 명령 순서대로 센서 응답을 캡처와 같은 지연으로 돌려준다."""
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
        self.steps = []   # [tx, tx 시각, [(tx 뒤 지연, rx 조각)]]
        for r in rows:
            if r["dir"] == "tx":
                self.steps.append([bytes.fromhex(r["hex"]), float(r["t"]), []])
            elif r["dir"] == "rx" and self.steps:
                self.steps[-1][2].append((float(r["t"]) - self.steps[-1][1], bytes.fromhex(r["hex"])))
        self.written = []    # [(시각, 바이트)]
        self.rx_done = []    # 명령 k의 응답 마지막 조각을 전달한 시각
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
    """캡처 하나를 UsbSensor로 재생한다. setup(sensor)으로 sink를 더 붙일 수 있다."""
    rows = load_capture(case)
    clock = VirtualClock(wall_base=float(rows[0]["t"]))
    tr = ReplayTransport(rows, clock)
    # 캡처할 때 PXSR 설정의 센서 타입은 S1813E였다 (B 센서도 버전 응답으로 바뀐다)
    s = UsbSensor(tr, clock=clock, specification="S1813E")
    frames, calls = [], {}
    s.add_sink(frames.append)
    if setup is not None:
        setup(s)
    cal_idx = {i for i, st in enumerate(tr.steps) if st[0][6] == codec.USB_FUNC_WRITE}

    def on_tx(i):
        if i + 1 in cal_idx:            # 사용자가 이 요청 직후 캘리브레이션 버튼을 누른 것으로 재생
            calls[i + 1] = clock.now()
            s.calibrate()
        if i == len(tr.steps):          # 캡처 끝: 응답 없는 요청 하나가 더 나간 뒤 해제
            s.disconnect()

    tr.on_tx = on_tx
    s.run()   # 가상 시계라 현재 스레드에서 바로 실행
    return tr, s, frames, calls
