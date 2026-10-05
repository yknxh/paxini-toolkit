"""Command-line interface: the same connection, calibration, logging and gauge test as the GUI, without Qt.

    python -m paxkit                      GUI (default)
    python -m paxkit <command> [options]  CLI (see `python -m paxkit --help`)

Every command uses the same modules as the GUI (`UsbSensor`, `CsvRecorder`, `CalibrationRun`, `BenchSession`,
`analyze_session`), so the files written are identical to GUI recordings: PXSR-format CSV + `.json` sidecar in
`data/logs/`, calibration history in `data/calibration/history.jsonl`, gauge test sessions in `data/bench/`.
Nothing here imports PySide6, so the CLI also runs on machines without a display.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Config

SIM_MODELS = ("S1813E", "S2015")   # keys of `device.sim.SIM_VERSIONS`
WAIT_S = 10.0                      # how long to wait for the first data after connecting
GUIDE = ("Press the sensor surface at many different positions with 0-{max:g} N. Hold each press still for "
         "about 1 s, then change the force slowly, and move to another position. Keep the gauge tip normal to "
         "the pressed surface. Do not exceed {max:g} N. Recommended 3-5 min, until every zone is 'enough'.")


class CliError(Exception):
    """A user-facing error: printed without a traceback, exit code 1."""


# ── terminal output ──────────────────────────────────────────────────
class StatusLine:
    """One status line updated in place on a terminal; a plain line every `interval` s when redirected."""

    def __init__(self, interval: float = 2.0) -> None:
        self.tty = sys.stdout.isatty()
        self.interval = interval
        self._width = 0
        self._last = 0.0

    def show(self, text: str) -> None:
        if self.tty:
            sys.stdout.write("\r" + text + " " * max(0, self._width - len(text)))
            sys.stdout.flush()
            self._width = len(text)
        elif time.monotonic() - self._last >= self.interval:
            print(text, flush=True)
            self._last = time.monotonic()

    def end(self) -> None:
        if self.tty and self._width:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self._width = 0


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _confirm(prompt: str, assume_yes: bool) -> bool:
    """Wait for Enter (False on Ctrl+C / EOF). Skipped with --yes or when stdin is not a terminal."""
    if assume_yes or not sys.stdin.isatty():
        return True
    try:
        input(prompt)
        return True
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def _sleep_status(seconds: float, text: str) -> None:
    status = StatusLine()
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        status.show(f"{text} {left:4.1f} s")
        time.sleep(min(0.1, left))
    status.end()


# ── devices ──────────────────────────────────────────────────────────
def _receiving(dev, window: float = 1.0) -> bool:
    """Connected and a value arrived within `window` s (same rule as the GUI test tab)."""
    if dev is None or not dev.is_alive() or dev.status != "connected":
        return False
    t, _ = dev.buffer.latest()
    return t is not None and dev.clock.wall() - t < window


def _sensor_alive(sensor) -> bool:
    return sensor.is_alive() and sensor.status == "connected" and not sensor.stalled


def open_sensor(args, cfg: Config, verbose: bool = True):
    """Connect the USB sensor (or a simulated one) like the GUI device panel. Returns (sensor, connection info)."""
    from .device.sim import SimUsbTransport
    from .device.transport import SerialTransport, default_sensor_port
    from .device.usb import STALL_S, UsbSensor
    from .state import load_state, save_state

    sim = getattr(args, "sim", None)
    if sim:
        transport = SimUsbTransport(sim)
        port = f"sim:{sim}"
    else:
        port = getattr(args, "port", None) or cfg.get("device.port") or default_sensor_port()
        if not port:
            raise CliError("Sensor port not found (expected exactly one CH343 port). "
                           "Pass --port, set device.port in config.yaml, or see `python -m paxkit ports`.")
        transport = SerialTransport(port)

    def on_event(kind: str, info: dict) -> None:
        if kind == "sensor_type" and not sim:
            save_state(specification=info["sensor"])   # PXSR `K`: remember the sensor type (same as the GUI)
        if not verbose:
            return
        text = {
            "version": lambda: f"version (serviceID {info.get('service_id')}): {info.get('version')}",
            "sensor_type": lambda: f"sensor type {info.get('sensor')} ({info.get('taxels')} taxels)",
            "warning": lambda: f"warning: {info.get('message')}",
            "error": lambda: f"error: {info.get('message')}",
            "stalled": lambda: f"no frames for more than {info.get('stall_s')} s - reception stalled",
        }.get(kind)
        if text is not None:
            print(f"  [sensor] {text()}", flush=True)

    sensor = UsbSensor(transport, specification=load_state()["specification"], on_event=on_event,
                       stall_s=float(cfg.get("device.stall_s") or STALL_S))
    print(f"Connecting sensor on {port} ...", flush=True)
    sensor.start()
    info = {"mode": "usb", "port": port, "simulated": bool(sim), "client": "cli"}
    return sensor, info


def open_gauge(args, cfg: Config, sensor=None):
    """Connect the force gauge (or a simulated one that follows the simulated sensor). Returns (gauge, info)."""
    from .device.sim import SimUsbTransport
    from .gauge import SerialGauge, SimGauge

    if getattr(args, "sim_gauge", False):
        tr = getattr(sensor, "transport", None)
        gauge = SimGauge(load=tr.load_N if isinstance(tr, SimUsbTransport) else None, rate_hz=10.0)
        port = "sim"
    else:
        port = getattr(args, "gauge_port", None) or cfg.get("gauge.port")
        if not port:
            raise CliError("Gauge port not set. Pass --gauge-port or set gauge.port in config.yaml.")
        gauge = SerialGauge({**cfg.section("gauge"), "port": port})
    print(f"Connecting gauge on {port} ...", flush=True)
    gauge.start()
    return gauge, {"port": port, "simulated": isinstance(gauge, SimGauge)}


def wait_for_data(dev, name: str, timeout: float = WAIT_S) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _receiving(dev):
            return
        if not dev.is_alive():
            break
        time.sleep(0.05)
    err = getattr(dev, "error", "") or ""
    raise CliError(f"No data from the {name} within {timeout:g} s" + (f" ({err})" if err else "")
                   + ". Check the port, cable and power" + (" (close PXSR if it is running)." if name == "sensor" else "."))


def close_devices(sensor=None, gauge=None) -> None:
    if sensor is not None and sensor.is_alive():
        sensor.disconnect()
    if gauge is not None:
        gauge.stop()
    if sensor is not None:
        sensor.join(5)
    if gauge is not None:
        gauge.join(3)


def sensor_desc(sensor) -> str:
    st = sensor.sensor_type
    return f"{st.label} ({st.forces} taxels), serviceID {sensor.service_id}, {sensor.version or 'version -'}"


def force_N(sensor) -> Tuple[Optional[Tuple[float, float, float]], Optional[float]]:
    """Latest resultant force (X, Y, Z) and |F| in N (raw × 0.1, display only)."""
    _, v = sensor.buffer.latest()
    if v is None:
        return None, None
    xyz = tuple(float(x) / 10.0 for x in v)
    return xyz, sum(c * c for c in xyz) ** 0.5


# ── calibration ──────────────────────────────────────────────────────
def run_calibration(sensor, info: Dict[str, Any]):
    """Send the PXSR calibration command once, wait for the result and append it to the history file."""
    from .calibration import OUTCOME_TEXT, CalibrationRun, append_history

    run = CalibrationRun(sensor, info).start()
    r = run.wait(timeout=run.ack_timeout + run.window + 2.0)
    path = append_history(r)
    z = lambda v: "-" if v is None else f"{v[2] / 10:.2f} N"
    status = "" if r.status is None else f" (status {r.status}, function code {r.function_code})"
    print(f"Calibration: {OUTCOME_TEXT.get(r.outcome, r.outcome)}{status}")
    print(f"  Z before {z(r.before)} -> after {z(r.after)}; history: {path}")
    return r


def _calibration_event(result) -> Dict[str, Any]:
    d = result.to_dict()
    d.pop("requested", None)
    return d


# ── commands ─────────────────────────────────────────────────────────
def cmd_ports(args, cfg: Config) -> int:
    from .device.transport import list_serial_ports

    ports = list_serial_ports()
    if not ports:
        print("No serial ports found.")
    for p in ports:
        print(f"{p.device:<24} {p.description}" + ("   [sensor candidate: CH343]" if p.is_sensor else ""))
    print(f"\nconfig.yaml: device.port = {cfg.get('device.port') or '(auto)'}, gauge.port = {cfg.get('gauge.port') or '-'}")
    return 0


def cmd_monitor(args, cfg: Config) -> int:
    sensor, _ = open_sensor(args, cfg)
    gauge = None
    try:
        wait_for_data(sensor, "sensor")
        print(f"Sensor: {sensor_desc(sensor)}")
        if args.gauge or args.sim_gauge or args.gauge_port:
            gauge, _ = open_gauge(args, cfg, sensor)
            wait_for_data(gauge, "gauge")
        print("Monitoring" + (f" for {args.duration:g} s" if args.duration else "") + " (Ctrl+C to stop)")
        status = StatusLine()
        t0 = time.monotonic()
        try:
            while not args.duration or time.monotonic() - t0 < args.duration:
                if not _sensor_alive(sensor):
                    break
                now = sensor.clock.wall()
                xyz, mag = force_N(sensor)
                text = f"{sensor.buffer.rate(now):5.0f} Hz  "
                text += "-" if xyz is None else f"X {xyz[0]:+6.1f}  Y {xyz[1]:+6.1f}  Z {xyz[2]:+6.1f}  |F| {mag:5.1f} N"
                if gauge is not None:
                    g = gauge.latest()
                    text += "   gauge " + ("-" if g is None else f"{g:5.1f} N")
                status.show(text)
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        status.end()
        if not _sensor_alive(sensor):
            print(f"Sensor stopped: {sensor.status} {sensor.error}".rstrip())
            return 1
        return 0
    finally:
        close_devices(sensor, gauge)


def cmd_calibrate(args, cfg: Config) -> int:
    sensor, info = open_sensor(args, cfg)
    try:
        wait_for_data(sensor, "sensor")
        print(f"Sensor: {sensor_desc(sensor)}")
        if not _confirm("Remove everything from the sensor, then press Enter to calibrate (Ctrl+C to abort) ",
                        args.yes):
            return 1
        time.sleep(0.6)   # let the "before" window (0.5 s) fill with unloaded data
        r = run_calibration(sensor, info)
        return 0 if r.outcome == "ack" else 1
    finally:
        close_devices(sensor)


def cmd_record(args, cfg: Config) -> int:
    from .recording import CsvRecorder

    sensor, info = open_sensor(args, cfg)
    rec = None
    try:
        wait_for_data(sensor, "sensor")
        print(f"Sensor: {sensor_desc(sensor)}")
        cal = None
        if args.calibrate:
            if not _confirm("Remove everything from the sensor, then press Enter to calibrate (Ctrl+C to abort) ",
                            args.yes):
                return 1
            time.sleep(0.6)
            cal = run_calibration(sensor, info)
        if not _confirm("Press Enter to start recording (Ctrl+C to abort) ", args.yes):
            return 1
        out = Path(args.out).resolve() if args.out else None
        rec = CsvRecorder(sensor, out, info=info)
        rec.memo = args.memo or ""
        path = rec.start()
        if cal is not None:
            rec.note_event("calibration", cal.requested, **_calibration_event(cal))
        print(f"Recording to {path}" + (f" for {args.duration:g} s" if args.duration else "") + " (Ctrl+C to stop)")
        status = StatusLine()
        reason = ""
        try:
            while True:
                elapsed = time.time() - rec.started_at
                if args.duration and elapsed >= args.duration:
                    break
                if not rec.active or not _sensor_alive(sensor):
                    reason = "sensor reception stalled" if sensor.stalled else f"sensor {sensor.status} {sensor.error}"
                    break
                now = sensor.clock.wall()
                _, mag = force_N(sensor)
                status.show(f"{_mmss(elapsed)}  {rec.line_count} rows  {sensor.buffer.rate(now):4.0f} Hz  "
                            + ("|F| -" if mag is None else f"|F| {mag:5.1f} N"))
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        status.end()
        rec.stop()
        if reason:
            print(f"Recording stopped early: {reason.strip()} (saved up to that point)")
        if rec.file_exists:
            print(f"Saved {rec.line_count} rows: {rec.path}")
            print(f"Sidecar: {rec.path.with_suffix('.json')}")
        else:
            print("No frames were recorded, so no file was created (same as PXSR).")
        return 1 if reason or not rec.file_exists else 0
    finally:
        if rec is not None:
            rec.stop()
        close_devices(sensor)


def _print_coverage(cov) -> None:
    zs = cov.zones
    print(f"Stable samples {cov.total_stable} (contact {cov.total_contact}, no position {cov.no_zone})")
    if zs is None:
        return
    enough = cov.enough()
    b = cov.bins
    print(f"  {'zone':<20} {'stable':>6}  force bins {b[0]:g}-{b[1]:g} / {b[1]:g}-{b[2]:g} / {b[2]:g}+ N")
    for k, z in enumerate(zs.zones):
        bins = " ".join(f"{int(c):>4}" for c in cov.bin_counts[k])
        print(f"  {z.name:<20} {int(cov.stable[k]):>6}  {bins}   {'enough' if enough[k] else 'not enough'}")


def print_result(res) -> None:
    """Short summary of a gauge test analysis (`BenchResult`)."""
    from .bench.plots import lag_text

    m = res.metrics.iloc[0]
    f = lambda v, nd=3: "-" if v is None or v != v else f"{float(v):.{nd}f}"
    print(f"{res.folder.name}")
    print(f"  stable samples {int(m['n'])}: bias {f(m['bias_N'])} N, SD {f(m['sd_N'])} N, RMSE {f(m['rmse_N'])} N, "
          f"slope {f(m['slope'], 4)}, R² {f(m['r2'], 5)}")
    print(f"  all contact samples {int(m['n_contact'])}: bias {f(m['bias_contact_N'])} N, "
          f"RMSE {f(m['rmse_contact_N'])} N")
    for s in res.sensors:
        print(f"  {s['label']} ({s.get('model')}): {s['rate_hz']} Hz, residual lag {lag_text(s)}")
    for w in res.warnings:
        print(f"  warning: {w}")
    print(f"  report: {res.folder / 'report.html'}")


def cmd_bench(args, cfg: Config) -> int:
    from .bench import BenchSession, Coverage, analyze_session, bench_settings, load_zones, noload_check

    settings = bench_settings(cfg)
    sensor, sensor_info = open_sensor(args, cfg)
    gauge = None
    sess = None
    cov = None
    try:
        wait_for_data(sensor, "sensor")
        print(f"Sensor: {sensor_desc(sensor)}")
        gauge, gauge_info = open_gauge(args, cfg, sensor)
        wait_for_data(gauge, "gauge")
        print(f"Gauge: {gauge.port}, {gauge.latest():.1f} N")

        pending: List[Tuple[str, float, Dict[str, Any]]] = []   # preparation events, written when recording starts
        if args.calibrate:
            if not _confirm("Remove everything from the sensor, then press Enter to calibrate (Ctrl+C to abort) ",
                            args.yes):
                return 1
            time.sleep(0.6)
            r = run_calibration(sensor, sensor_info)
            pending.append(("calibration", r.requested, _calibration_event(r)))
        if not args.skip_noload:
            secs = float(settings["noload_check_s"])
            if not _confirm(f"No-load check ({secs:g} s): take your hands off the sensor and gauge, then press Enter ",
                            args.yes):
                return 1
            t0 = sensor.clock.wall()
            _sleep_status(secs, "No-load check, hands off ...")
            nl = noload_check(sensor.buffer, gauge.buffer, t0, sensor.clock.wall(), float(settings["noload_warn_N"]))
            g = "-" if nl["gauge_mean_N"] is None else f"{nl['gauge_mean_N']:+.2f} N"
            s = "-" if nl["sensor_F_mean_N"] is None else f"{nl['sensor_F_mean_N']:.2f} N"
            print(f"No-load: gauge {g}, sensor |F| {s}" + (" - " + "; ".join(nl["warnings"]) if nl["warnings"] else " - OK"))
            pending.append(("noload_check", t0, nl))

        print("\n" + GUIDE.format(max=float(settings["max_N"])) + "\n")
        if not _confirm("Press Enter to start recording (Ctrl+C to abort) ", args.yes):
            return 1
        model = sensor.sensor_type.label
        sess = BenchSession(sensor, gauge, settings, label=args.label, model=model, sensor_info=sensor_info,
                            gauge_info=gauge_info, gauge_config=cfg.section("gauge"),
                            root=Path(args.out).resolve() if args.out else None)
        for kind, t, info in pending:
            sess.note_event(kind, t, **info)
        cov = Coverage(load_zones(model), settings)
        folder = sess.start()
        sensor.add_sink(cov.add_frame)
        gauge.add_sink(cov.add_gauge)
        print(f"Recording to {folder}" + (f" for {args.duration:g} s" if args.duration else "")
              + " (Ctrl+C to stop and analyze)")
        status = StatusLine()
        reason = ""
        max_N = float(settings["max_N"])
        try:
            while True:
                elapsed = time.time() - sess.started_at
                if args.duration and elapsed >= args.duration:
                    break
                if not _sensor_alive(sensor):
                    reason = "sensor reception stalled" if sensor.stalled else f"sensor {sensor.status} {sensor.error}"
                    if sensor.stalled:
                        sess.note_event("sensor_stalled", sensor.last_rx, stall_s=sensor.stall_s)
                    break
                if not gauge.is_alive():
                    reason = f"gauge {gauge.status} {gauge.error}"
                    break
                cov.update()
                g = gauge.latest()
                _, mag = force_N(sensor)
                enough = cov.enough()
                text = (f"{_mmss(elapsed)}  gauge " + ("-" if g is None else f"{g:5.1f} N")
                        + ("  |F| -" if mag is None else f"  |F| {mag:5.1f} N")
                        + f"  stable {cov.total_stable}"
                        + (f"  zones enough {int(enough.sum())}/{len(enough)}" if cov.zones is not None else "")
                        + (f"  OVER {max_N:g} N!" if g is not None and g > max_N else ""))
                status.show(text)
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        status.end()
        sensor.remove_sink(cov.add_frame)
        gauge.remove_sink(cov.add_gauge)
        sess.stop(cancelled=False)
        cov.update()
        if reason:
            print(f"Recording stopped early: {reason.strip()} (saved up to that point)")
        print(f"Saved: {sess.folder} (sensor {sess.sensor_rows} rows, gauge {sess.gauge_rows} samples)")
        _print_coverage(cov)
        if sess.sensor_csv is None:
            print("No sensor rows were recorded - nothing to analyze.")
            return 1
        if args.no_analyze:
            print(f"Analysis skipped. Run later: python -m paxkit analyze \"{sess.folder}\"")
            return 0
        print("Analyzing ...", flush=True)
        print_result(analyze_session(sess.folder))
        return 1 if reason else 0
    finally:
        if sess is not None and sess.status == "recording":
            sess.stop(cancelled=False)
        close_devices(sensor, gauge)


def cmd_analyze(args, cfg: Config) -> int:
    import yaml

    from .bench import analyze_session
    from .paths import data_path

    folders = [Path(f) for f in args.folders]
    if args.latest:
        cands = sorted(p for p in data_path("bench").iterdir() if (p / "meta.json").is_file())
        if not cands:
            raise CliError(f"No sessions in {data_path('bench')}")
        folders += cands[-1:]
    if not folders:
        raise CliError("Give one or more session folders, or --latest.")
    override = {}
    for kv in args.set:
        k, sep, v = kv.partition("=")
        if not sep:
            raise CliError(f"--set expects NAME=VALUE, got {kv!r}")
        override[k.strip()] = yaml.safe_load(v)
    for f in folders:
        if not (f / "meta.json").is_file():
            raise CliError(f"Not a gauge test session folder (no meta.json): {f}")
        print_result(analyze_session(f, override or None))
    return 0


def cmd_gui(args, cfg: Config) -> int:
    from .gui.app import main as gui_main

    return gui_main(["--config", str(cfg.path)])


# ── argument parsing ─────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to config.yaml (default: repo root)")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="debug logging")

    sensor = argparse.ArgumentParser(add_help=False)
    g = sensor.add_argument_group("sensor")
    g.add_argument("--port", help="sensor serial port (default: device.port in config.yaml, else the only CH343 port)")
    g.add_argument("--sim", nargs="?", const="S1813E", choices=SIM_MODELS, metavar="MODEL",
                   help="use a simulated sensor instead of hardware (S1813E or S2015, default S1813E)")

    gauge = argparse.ArgumentParser(add_help=False)
    g = gauge.add_argument_group("force gauge")
    g.add_argument("--gauge-port", help="gauge serial port (default: gauge.port in config.yaml)")
    g.add_argument("--sim-gauge", action="store_true", help="use a simulated gauge (follows --sim sensor load)")

    yes = argparse.ArgumentParser(add_help=False)
    yes.add_argument("-y", "--yes", action="store_true", help="do not wait for Enter at the prompts")

    ap = argparse.ArgumentParser(
        prog="python -m paxkit",
        description="paxini-toolkit: unofficial tools for Paxini Gen3 tactile sensors "
                    "(PXSR-compatible connection, calibration and logging + force gauge accuracy test). "
                    "Without a command, the GUI starts.",
        parents=[common])
    sub = ap.add_subparsers(dest="cmd", metavar="command")

    sub.add_parser("gui", parents=[common], help="start the GUI (same as no command)")
    sub.add_parser("ports", parents=[common], help="list serial ports and mark sensor candidates")

    p = sub.add_parser("monitor", parents=[common, sensor, gauge], help="show live sensor force (and gauge) values")
    p.add_argument("--gauge", action="store_true", help="also read the gauge (port from config.yaml)")
    p.add_argument("--duration", type=float, default=0, help="seconds to run (default: until Ctrl+C)")

    sub.add_parser("calibrate", parents=[common, sensor, yes],
                   help="send the PXSR calibration (zero) command once, sensor unloaded")

    p = sub.add_parser("record", parents=[common, sensor, yes],
                       help="log sensor data to a PXSR-format CSV (data/logs/)")
    p.add_argument("--duration", type=float, default=0, help="seconds to record (default: until Ctrl+C)")
    p.add_argument("--calibrate", action="store_true", help="calibrate once before recording")
    p.add_argument("--memo", default="", help="note stored in the .json sidecar (not in the CSV)")
    p.add_argument("--out", help="output folder (default: data/logs/)")

    p = sub.add_parser("bench", parents=[common, sensor, gauge, yes],
                       help="gauge accuracy test: record sensor + gauge, then analyze (data/bench/)")
    p.add_argument("--label", required=True, help="sensor name used in the session folder, e.g. A1")
    p.add_argument("--duration", type=float, default=0, help="seconds to record (default: until Ctrl+C)")
    p.add_argument("--calibrate", action="store_true", help="calibrate once before the no-load check")
    p.add_argument("--skip-noload", action="store_true", help="skip the no-load check")
    p.add_argument("--no-analyze", action="store_true", help="only record; analyze later with `analyze`")
    p.add_argument("--out", help="parent folder for the session folder (default: data/bench/)")

    p = sub.add_parser("analyze", parents=[common], help="(re)analyze gauge test session folders")
    p.add_argument("folders", nargs="*", help="session folders (data/bench/<...>)")
    p.add_argument("--latest", action="store_true", help="the most recent session in data/bench/")
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                   help="override an analysis setting (recorded in result.json), e.g. --set bin_N=2")
    return ap


COMMANDS = {"gui": cmd_gui, "ports": cmd_ports, "monitor": cmd_monitor, "calibrate": cmd_calibrate,
            "record": cmd_record, "bench": cmd_bench, "analyze": cmd_analyze}


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:   # legacy console code pages (e.g. cp1252 when piped) cannot encode "²", "−", "→": replace, don't crash
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    verbose = getattr(args, "verbose", False)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = Config.load(getattr(args, "config", None))
        return COMMANDS[args.cmd or "gui"](args, cfg)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
