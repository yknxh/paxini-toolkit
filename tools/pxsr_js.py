"""설치된 PXSR의 원본 JS 코드를 그대로 실행하는 기준(oracle) 도구.

PXSR 번들(`dist/index.3bcb906d.js`)에서 함수 소스를 잘라 내, PXSR 실행 파일을 Node 모드
(`ELECTRON_RUN_AS_NODE=1`, Node v16)로 띄워 실행한다. PXSR 코드는 레포에 복사하지 않는다.

Python 이식본(`paxkit.device.codec`)과 같은 입력을 넣어 결과를 비교하는 데 쓴다
(`tests/test_pxsr_js_diff.py`). PXSR이 설치된 Windows PC에서만 동작한다.
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
    """start 이후 첫 `{`부터 짝이 맞는 `}`까지 (문자열·템플릿 리터럴 안의 괄호는 건너뜀)."""
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
    raise ValueError("괄호 짝을 찾지 못함")


def extract(name_pattern: str, src: Optional[str] = None) -> str:
    """`name=(...)=>{...}` 형태 정의 하나를 잘라 온다. 예: extract(r"Na0=\\(\\)=>")."""
    src = src if src is not None else BUNDLE.read_text(encoding="utf-8")
    m = re.search(r"(?<![\w$])" + name_pattern, src)
    if not m:
        raise KeyError(name_pattern)
    return _block_from(src, m.start())


def usb_source() -> str:
    """USB 직결 코드 `Na0`와 그 의존 함수 `A2`, `ti`를 실행 가능한 형태로 묶는다."""
    src = BUNDLE.read_text(encoding="utf-8")
    parts = [extract(r"A2=\(t,e=4\)=>", src), extract(r"ti=\(t,e\)=>", src), extract(r"Na0=\(\)=>", src)]
    stubs = (
        "const g6={Buffer};"
        "const f0=v=>({value:v});"                      # Vue ref 대용
        "const __warn=[];const J3={warning:m=>__warn.push(m),success:()=>{}};"
        "const z1={global:{t:k=>k}};"
        "const eS=()=>[];"                               # OTA 파일 분할 (쓰지 않음)
        "console.error=()=>{};"
    )
    return stubs + "".join("const " + p + ";" for p in parts)


def csv_source() -> str:
    """데이터 로깅 기준 코드: PXSR이 쓰는 `csv-writer`(설치본 node_modules 그대로)와 파일명 함수 `rs0`."""
    src = BUNDLE.read_text(encoding="utf-8")
    module = (PXSR_DIR / "resources/app/node_modules/csv-writer").as_posix()
    return f"const csvWriter=require({json.dumps(module)});" + extract(r"function rs0\(t,e\)", src)


def run(harness: str, payload: Any, timeout: float = 120, prelude: Optional[str] = None) -> Any:
    """PXSR Node로 `prelude(기본 usb_source()) + harness`를 실행한다. harness는 전역 `INPUT`을 읽고 `OUTPUT`에 결과를 넣는다."""
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
            raise RuntimeError(f"PXSR JS 실행 실패: {r.stdout}{r.stderr}")
        return json.loads(out.read_text(encoding="utf-8"))
