"""Reference (oracle) tool that runs the installed PXSR's original JS code as is.

Function sources are cut out of the PXSR bundle (`dist/index.3bcb906d.js`) and run by launching the PXSR executable
in Node mode (`ELECTRON_RUN_AS_NODE=1`, Node v16). PXSR code is not copied into the repo.

Used to feed the same inputs as the Python port (`paxkit.device.codec`) and compare results
(`tests/test_pxsr_js_diff.py`). Works only on a Windows PC with PXSR installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

PXSR_DIR = Path(os.environ.get("PXSR_DIR", Path.home() / "AppData/Local/Programs/pxsr-gen3"))
PXSR_EXE = PXSR_DIR / "pxsr-gen3.exe"
BUNDLE = PXSR_DIR / "resources/app/dist/index.3bcb906d.js"


def available() -> bool:
    return PXSR_EXE.is_file() and BUNDLE.is_file()


def _block_from(src: str, start: int) -> str:
    """From the first `{` after start to its matching `}` (braces inside string/template literals are skipped)."""
    i = src.index("{", start)
    depth, quote, k = 0, None, i
    while k < len(src):
        c = src[k]
        if quote:
            if c == "\\":
                k += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
        k += 1
    raise ValueError("matching brace not found")


def extract(name_pattern: str, src: Optional[str] = None) -> str:
    """Cut out one definition of the form `name=(...)=>{...}`. E.g. extract(r"Na0=\\(\\)=>")."""
    src = src if src is not None else BUNDLE.read_text(encoding="utf-8")
    m = re.search(r"(?<![\w$])" + name_pattern, src)
    if not m:
        raise KeyError(name_pattern)
    return _block_from(src, m.start())


def usb_source() -> str:
    """Bundle the USB direct code `Na0` and its dependencies `A2`, `ti` into runnable form."""
    src = BUNDLE.read_text(encoding="utf-8")
    parts = [extract(r"A2=\(t,e=4\)=>", src), extract(r"ti=\(t,e\)=>", src), extract(r"Na0=\(\)=>", src)]
    stubs = (
        "const g6={Buffer};"
        "const f0=v=>({value:v});"                      # stand-in for Vue ref
        "const __warn=[];const J3={warning:m=>__warn.push(m),success:()=>{}};"
        "const z1={global:{t:k=>k}};"
        "const eS=()=>[];"                               # OTA file splitting (unused)
        "console.error=()=>{};"
    )
    return stubs + "".join("const " + p + ";" for p in parts)


def hand_source() -> str:
    """Append the HAND board code `ka0` and checksum `$9` after `usb_source()` (for comparing the calibration command for now)."""
    src = BUNDLE.read_text(encoding="utf-8")
    parts = [extract(r"\$9=t=>", src), extract(r"ka0=\(\)=>", src)]
    return usb_source() + "".join("const " + p + ";" for p in parts)


def csv_source() -> str:
    """Reference code for data logging: the `csv-writer` PXSR uses (installed node_modules as is) and file name function `rs0`."""
    src = BUNDLE.read_text(encoding="utf-8")
    module = (PXSR_DIR / "resources/app/node_modules/csv-writer").as_posix()
    return f"const csvWriter=require({json.dumps(module)});" + extract(r"function rs0\(t,e\)", src)


def run(harness: str, payload: Any, timeout: float = 120, prelude: Optional[str] = None) -> Any:
    """Run `prelude (default usb_source()) + harness` with PXSR Node. harness reads global `INPUT` and puts its result in `OUTPUT`."""
    with tempfile.TemporaryDirectory() as d:
        inp, out, js = Path(d, "in.json"), Path(d, "out.json"), Path(d, "run.js")
        inp.write_text(json.dumps(payload), encoding="utf-8")
        js.write_text(
            (usb_source() if prelude is None else prelude)
            + "const fs=require('fs');const INPUT=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));let OUTPUT=null;"
            + "(async()=>{" + harness + "\nfs.writeFileSync(process.argv[3],JSON.stringify(OUTPUT));})()"
            + ".catch(e=>{console.log(e&&e.stack||e);process.exit(1)});",
            encoding="utf-8",
        )
        env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
        r = subprocess.run([str(PXSR_EXE), str(js), str(inp), str(out)], env=env,
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError(f"PXSR JS run failed: {r.stdout}{r.stderr}")
        return json.loads(out.read_text(encoding="utf-8"))
