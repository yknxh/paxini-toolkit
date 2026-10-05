# paxini-toolkit

Cross-platform (Windows / macOS / Linux) tools for **Paxini Gen3 tactile sensors**.

> **Unofficial.** This project is not affiliated with or endorsed by Paxini and is not the official Paxini SDK.

`paxkit` replaces the basic functions of Paxini's Windows-only desktop app **PXSR** (`pxsr-gen3`) and adds a
**force-gauge accuracy test**:

- **Connect** to a sensor over USB and stream frames without PXSR.
- **Calibrate** (zero) the sensor with the same command sequence PXSR sends.
- **Log data** to CSV files that are **byte-for-byte identical** to PXSR's own data logging output.
- **Gauge test (bench):** record the sensor and a force gauge on the same clock while you press the sensor,
  then get error plots and metrics overall and per sensor zone.

Everything is available both in a **GUI** (PySide6 + pyqtgraph) and a **command-line interface** for real data
collection runs.

---

## Contents

- [Supported hardware](#supported-hardware)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick start](#quick-start)
- [GUI](#gui)
- [Command-line interface](#command-line-interface)
- [Gauge test (bench)](#gauge-test-bench)
- [Output files](#output-files)
- [Simulation mode (no hardware)](#simulation-mode-no-hardware)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Supported hardware

| Device | Details |
|---|---|
| Paxini Gen3 sensor, type A | **S1813E**, 31 taxels, rated 25 N |
| Paxini Gen3 sensor, type B | **S2015E** (shown as `S2015` by PXSR), 52 taxels, rated 25 N |
| Connection | **Direct USB**, one sensor per cable. The sensor appears as a WCH **CH343** USB-serial port (VID:PID `1A86:55D3`), 921600 baud. |
| Force gauge (optional, for the gauge test) | Serial force gauge streaming fixed-width ASCII records (tested with a ZP-500N-type gauge over an FTDI adapter: 2400 baud 8N1, ~10 Hz, records like `-004.9`). |

Status of the other PXSR connection modes:

- **HAND board** (several sensors per hand board): the gauge-test *analysis* supports multiple sensors, but the
  live connection is not implemented yet.
- **SPI adapter**: out of scope.
- **Firmware upgrade**: deliberately out of scope (risk of bricking sensors).

> Close PXSR before using paxkit: a serial port can only be opened by one program at a time.

---

## Installation

### 1. Prerequisites

- **Python 3.11 or newer** ([python.org](https://www.python.org/downloads/), or your OS package manager)
- **git**
- A serial driver for the sensor's **CH343** chip and for your gauge's USB-serial adapter:
  - **Windows**: usually installed automatically by Windows Update. Otherwise install the WCH CH343 driver.
  - **macOS**: recent macOS versions include a driver for WCH chips. If no `/dev/tty.*` port appears when the
    sensor is plugged in, install WCH's CH34x macOS driver.
  - **Linux**: supported by the kernel (`cdc_acm` / `ch341` modules); the port appears as `/dev/ttyACM*` or
    `/dev/ttyUSB*`.

### 2. Get the code

```bash
git clone https://github.com/yknxh/paxini-toolkit.git
cd paxini-toolkit
```

### 3. Create a virtual environment and install

Install in **editable mode** (`-e`). paxkit stores all data in the repository's `data/` folder, and the editable
install is what makes that path point into your clone.

**Windows** (PowerShell or cmd):

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

**macOS / Linux**:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

`[dev]` adds `pytest` for running the tests. Drop it if you only want to use the tools.

### 4. OS-specific setup

**Linux**

```bash
# serial port access (log out and back in afterwards)
sudo usermod -aG dialout $USER
# if the GUI fails to start with a Qt "xcb" platform plugin error (Ubuntu/Debian)
sudo apt install libxcb-cursor0
```

**Windows**

- If importing a package fails with `DLL load failed ... Application Control policy`, Windows Smart App Control
  is blocking an unsigned DLL. `pandas` is pinned to 2.2.x for this reason. For other packages, try an older version.

### 5. Check the installation

```bash
# Windows: .venv\Scripts\python   macOS/Linux: .venv/bin/python
python -m paxkit --help
python -m paxkit ports
```

In the examples below, `python` means the Python inside your virtual environment: `.venv\Scripts\python` on
Windows, `.venv/bin/python` on macOS/Linux. Alternatively, activate the venv first
(`.venv\Scripts\activate` or `source .venv/bin/activate`).

---

## Configuration

Settings live in [`config.yaml`](config.yaml) at the repository root (use `--config PATH` to load another file).
The values you are most likely to change:

| Key | Meaning |
|---|---|
| `device.port` | Sensor port. Leave empty to pick the CH343 port automatically (when exactly one is connected). |
| `device.stall_s` | If no frame arrives for this long, reception is reported as stalled and recording stops (default 1 s). |
| `gauge.port` | Force gauge port. |
| `gauge.baudrate`, `gauge.record_regex`, `gauge.invert`, `gauge.unit_scale` | Gauge serial format. Pressing must read as a positive force; set `invert: true` if your gauge reports compression as negative. |
| `gauge.latency_s` | Fixed gauge latency subtracted from gauge timestamps (measure it with `tools/gauge_live_check.py --sensor`). |
| `bench.*` | Gauge test settings: force range (`max_N`), contact / stability thresholds, bin width, zone sample minimum, lag correction. Each session stores a copy in its `meta.json`. |

Serial port names differ by OS:

| OS | Example sensor port | Example gauge port |
|---|---|---|
| Windows | `COM3` | `COM7` |
| macOS | `/dev/tty.usbmodemXXXX` or `/dev/tty.wchusbserialXXXX` | `/dev/tty.usbserial-XXXX` |
| Linux | `/dev/ttyACM0` | `/dev/ttyUSB0` |

Run `python -m paxkit ports` to list the ports on your machine. Sensor candidates (CH343) are marked.
The sensor baud rate (921600) is fixed in code, matching PXSR, and is not configurable.

---

## Quick start

```bash
python -m paxkit                       # start the GUI
python -m paxkit ports                 # list serial ports
python -m paxkit monitor               # live force values in the terminal (Ctrl+C to stop)
python -m paxkit calibrate             # zero the unloaded sensor
python -m paxkit record                # log to data/logs/ until Ctrl+C
python -m paxkit bench --label A1      # gauge accuracy test for sensor "A1"
```

No hardware at hand? Add `--sim` (and `--sim-gauge`), see [Simulation mode](#simulation-mode-no-hardware).

---

## GUI

```bash
python -m paxkit            # or: python -m paxkit gui [--config config.yaml]
```

After `pip install -e .`, the `paxkit` command also starts the GUI.

| Area | What it does |
|---|---|
| **Sensor (left)** | Choose the port, connect/disconnect. Shows the sensor type, firmware version, frame rate and connection events. |
| **Force gauge (left)** | Connect the gauge, show its rate and current value. |
| **Data logging (left)** | Start/stop PXSR-format logging to `data/logs/`, with an optional memo saved in the sidecar file. |
| **Live** tab | Resultant force X/Y/Z (and the gauge, if connected) over time, plus an optional taxel heatmap. |
| **Calibration** tab | Send the calibration command and show the result (acknowledged / failed / no reply) and the force before and after. |
| **Test** tab | Gauge test: prepare → no-load check / calibration → record → stop and analyze, with a live zone coverage map. |
| **Results** tab | Browse past gauge test sessions, view metrics and plots, re-analyze. |

If sensor reception stalls, any running recording is saved up to that point and stopped, and a notice is shown.

---

## Command-line interface

The CLI is meant for real data collection runs (long recordings, scripted sessions, headless machines).
It uses exactly the same code paths as the GUI, so the output files are identical. It does not import Qt.

```text
python -m paxkit <command> [options]
```

After `pip install -e .`, `paxkit-cli <command> ...` is equivalent.

Common options:

| Option | Meaning |
|---|---|
| `--config PATH` | Use another `config.yaml`. |
| `--port PORT` | Sensor port (default: `device.port`, else the only CH343 port). |
| `--sim [S1813E\|S2015]` | Use a simulated sensor. |
| `--gauge-port PORT` | Gauge port (default: `gauge.port`). |
| `--sim-gauge` | Use a simulated gauge. |
| `-y`, `--yes` | Don't wait for Enter at prompts (prompts are also skipped when stdin is not a terminal). |
| `-v`, `--verbose` | Debug logging. |

Press **Ctrl+C** to stop a running command. Recordings are saved cleanly.

### `ports`: list serial ports

```bash
python -m paxkit ports
```

### `monitor`: live values

```bash
python -m paxkit monitor                    # sensor only
python -m paxkit monitor --gauge            # sensor + gauge (gauge.port from config.yaml)
python -m paxkit monitor --port COM3 --duration 10
```

Prints the frame rate, resultant force X/Y/Z and |F| in newtons (and the gauge value).

### `calibrate`: zero the sensor

```bash
python -m paxkit calibrate
```

Remove everything from the sensor, then press Enter. paxkit sends the same calibration command as PXSR (the zero
point is handled by the sensor firmware), prints the outcome and the Z force before and after, and appends the
run to `data/calibration/history.jsonl`. No calibration values are stored or applied by the software.

### `record`: data logging

```bash
python -m paxkit record                                  # until Ctrl+C
python -m paxkit record --duration 60 --memo "trial 3"   # 60 s, memo stored in the sidecar
python -m paxkit record --calibrate                      # calibrate first, then record
python -m paxkit record --out D:/experiments/day1        # another output folder
```

Writes `data/logs/YYYY-MM-DD-HHMMSS.csv` (PXSR format) and a `.json` sidecar with the same name.
As with PXSR, the file is created when the first frame arrives. If no frame was received, no file is created.

### `bench`: gauge accuracy test

```bash
python -m paxkit bench --label A1                      # interactive: no-load check, record until Ctrl+C, analyze
python -m paxkit bench --label A1 --calibrate          # calibrate before the no-load check
python -m paxkit bench --label A1 --duration 240 -y    # fixed 4-minute recording, no prompts
python -m paxkit bench --label A1 --no-analyze         # record only
```

Steps: connect the sensor and gauge, optionally calibrate, run the **no-load check** (hands off for a few seconds),
then **record** while you press the sensor. The status line shows the gauge force, sensor |F|, the number of
stable samples and how many zones have enough data. It warns when the force exceeds `bench.max_N`.
After stopping, it prints the zone coverage table, analyzes the session and prints the main metrics plus the
path to `report.html`. See [Gauge test (bench)](#gauge-test-bench).

### `analyze`: (re)analyze sessions

```bash
python -m paxkit analyze --latest
python -m paxkit analyze data/bench/2026-10-06-101500_S1813E_A1
python -m paxkit analyze --latest --set bin_N=2 --set stable_slope_N_per_s=1.5
```

Re-runs the analysis and overwrites the result files in the session folder. By default the settings saved in the
session's `meta.json` are used. `--set NAME=VALUE` overrides a setting, and the override is recorded in `result.json`.

---

## Gauge test (bench)

The gauge test measures how well the sensor's resultant force |F| matches a reference force gauge.

**Procedure** (GUI *Test* tab or `python -m paxkit bench`):

1. Connect the sensor and the gauge. Mount the gauge so its tip can press the sensor.
2. *(Optional)* Calibrate with the sensor unloaded.
3. **No-load check**: hands off both devices for a few seconds. The values are recorded in `meta.json` and warnings
   are shown. They are never used to correct data.
4. **Record**: press the sensor surface at many different positions with forces between 0 and `max_N`
   (15 N by default). Hold each press still for about 1 s, then change the force slowly, and move on. Keep the gauge
   tip normal to the surface. Continue (typically 3-5 min) until every zone has enough data.
5. Stop. The session is analyzed automatically.

**Analysis**:

- Each gauge sample is paired with the sensor frames around it (both timestamped on the same PC clock).
  The residual sensor-gauge lag is measured per session by cross-correlation and corrected
  (`bench.lag_correct`).
- Samples are classified as *contact* (gauge above `contact_N`) and *stable* (force slope below
  `stable_slope_N_per_s`). Each sample is assigned to a **sensor zone** from the taxel distribution:
  7 zones = tip + (middle, root) × (left side, top, right side).
- Error = |F| − gauge (N). Reported per session and per zone: bias, SD, RMSE (also as %FS), MAE, P95, max |e|,
  linear fit |F| = a·gauge + b with R², the same for all contact samples, and the no-load residual.
- No pass/fail judgement is made, and no correction is ever applied to recorded data.

---

## Output files

All data is stored in `data/` inside the repository (ignored by git), regardless of the working directory.

```text
data/
├─ logs/                         data logging
│  ├─ 2026-10-06-101500.csv      PXSR-format CSV
│  └─ 2026-10-06-101500.json     sidecar: connection, sensor, version, events (calibration, stall), memo
├─ calibration/history.jsonl     one line per calibration run (time, outcome, force before/after)
├─ bench/<YYYY-MM-DD-HHMMSS>_<model>_<label>/
│  ├─ <timestamp>.csv / .json    sensor recording (same format as data/logs)
│  ├─ gauge.csv                  t_unix_s,force_N
│  ├─ meta.json                  settings, devices, events (no-load check, calibration)
│  ├─ samples.csv                paired samples (gauge, |F|, error, contact, stable, zone, CoP)
│  ├─ metrics.csv                metrics: overall and per zone
│  ├─ result.json                metrics + settings + warnings
│  ├─ plots/                     overall_error.png, zone_map.png, zones.png
│  └─ report.html                self-contained report (tables + embedded plots)
└─ state.json                    last sensor type (used like PXSR's saved specification)
```

### PXSR-compatible CSV

Given the same sensor bytes and receive times, paxkit writes the same file as PXSR, byte for byte:
UTF-8 without BOM, `,` separator, `\n` line endings, file named by start time `YYYY-MM-DD-HHMMSS.csv`.

- `Timestamp`: receive time `HH:MM:SS.mmm` (local time; the date is in the file name).
- Per sensor: resultant force `c-s-1x1-X/Y/Z`, then every taxel `c-s-NxN-X[i]/Y[i]/Z[i]`
  (`c` = channel, `s` = slot; for direct USB the slot is the sensor's serviceID − 1).
- Values are raw integers. **raw × 0.1 = N**.
- One row per received frame.

Anything PXSR does not write (connection info, versions, calibration events, memo) goes into the `.json` sidecar, never
into the CSV.

---

## Simulation mode (no hardware)

A simulated sensor answers the same commands as a real USB sensor and generates repeated presses at varying
positions. The simulated gauge follows the simulated sensor's load. Use them to try the GUI or CLI anywhere:

```bash
python -m paxkit monitor --sim
python -m paxkit record --sim S2015 --duration 10
python -m paxkit bench --sim --sim-gauge --label demo --duration 30 -y
```

In the GUI, pick `Simulated: S1813E` / `Simulated: S2015` as the sensor port and `Simulated gauge` as the gauge port.

---

## Troubleshooting

| Problem | What to check |
|---|---|
| `No data from the sensor` / port cannot be opened | Close PXSR (and any serial monitor). Check the port with `python -m paxkit ports`. Unplug and replug the sensor. |
| `Sensor port not found` | More than one (or no) CH343 port is connected. Pass `--port` or set `device.port`. |
| Linux: permission denied on `/dev/tty*` | `sudo usermod -aG dialout $USER`, then log out and back in. |
| Linux: GUI does not start (`xcb` plugin) | `sudo apt install libxcb-cursor0`. |
| Gauge: no values | Check `gauge.port`, baud rate, that the gauge's serial output is enabled and that it is set to N. |
| Gauge reads negative when pressed | Set `gauge.invert: true`. |
| Windows: `DLL load failed ... Application Control policy` | Smart App Control blocks an unsigned DLL. Install an older version of that package. |
| Reception stalled during recording | The recording is saved up to the stall. Disconnect and reconnect the sensor (as in PXSR, paxkit does not re-request data automatically). |

---

## Development

```bash
python -m pytest                     # run the test suite
```

- Device, recording, calibration, gauge and analysis logic are plain Python modules without Qt (`paxkit/device`,
  `recording`, `calibration`, `gauge`, `bench`). `paxkit/gui` only calls them, and `paxkit/cli.py` is the
  command-line front end.
- Byte-for-byte compatibility with PXSR is tested by replaying captured serial bytes against PXSR's own CSV files
  (`tests/fixtures/`, protected from line-ending conversion via `.gitattributes`). On a Windows PC with PXSR
  installed, some tests also run PXSR's original JavaScript as a reference (`tools/pxsr_js.py`). Elsewhere they are
  skipped.
- Helper scripts in `tools/`: `usb_live_check.py` (sensor protocol check), `gauge_live_check.py` (gauge check and
  sensor-gauge lag), `bench_analyze.py` (re-analysis), `make_zones.py` (zone definitions),
  `extract_geometry.py` (taxel geometry from PXSR), `usbpcap_extract.py` (USB capture parsing).
- Line endings are fixed by `.gitattributes` (text LF, `.bat` CRLF, fixtures untouched).
