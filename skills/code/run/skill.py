#!/usr/bin/env python3
"""
Echo Skill: code.run
Bitcraft Technologies — Echo Core Skills v1.0.0

Executes sandboxed Python or JavaScript code with structured output capture.
"""

import sys
import json
import os
import time
import socket
import subprocess
import tempfile
import argparse
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_TIMEOUT = 60
DEFAULT_TIMEOUT = 15
MAX_OUTPUT_BYTES = 65536  # 64 KB per stream
MEDULLA_HOST = "localhost"
MEDULLA_PORT = 77
PKG_CACHE = Path(os.path.expanduser("~/.echospace/skill/.pkg_cache"))

# ── Python sandbox wrapper ────────────────────────────────────────────────────

PYTHON_SANDBOX_WRAPPER = '''
import sys
import builtins

_BLOCKED_BUILTINS = {{"open", "breakpoint", "input"}}
_BLOCKED_MODULES = {{"subprocess", "socket", "shutil", "pty", "multiprocessing"}}

_real_import = builtins.__import__

def _safe_import(name, *args, **kwargs):
    base = name.split(".")[0]
    if base in _BLOCKED_MODULES:
        raise ImportError(f"Module '{{name}}' is blocked in code.run sandbox")
    return _real_import(name, *args, **kwargs)

for name in _BLOCKED_BUILTINS:
    if hasattr(builtins, name):
        setattr(builtins, name, None)

builtins.__import__ = _safe_import

# Allow os but block dangerous methods
import os as _os
_os.system = None
_os.popen = None
_os.execv = None
_os.execve = None
_os.fork = None

# --- USER CODE BELOW ---
{code}
'''

# ── JS sandbox wrapper ────────────────────────────────────────────────────────

JS_SANDBOX_WRAPPER = """
'use strict';
const _Module = require('module');
const _originalLoad = _Module._load;
const BLOCKED = new Set(['fs', 'child_process', 'net', 'http', 'https', 'dgram', 'cluster']);

_Module._load = function(request, ...args) {{
    if (BLOCKED.has(request)) {{
        throw new Error(`Module '${{request}}' is blocked in code.run sandbox`);
    }}
    return _originalLoad(request, ...args);
}};

{code}
"""

JS_SANDBOX_WRAPPER_FS = """
'use strict';
{code}
"""

# -- Run -────────────────────────────────────────────────────────────────────────────
def run(params: dict) -> dict:
    args = parse_args()
    timeout = min(args.timeout_sec, MAX_TIMEOUT)

    # Package pre-install
    if args.packages:
        if args.language == "python":
            pkg_errors = ensure_packages_python(args.packages)
        else:
            pkg_errors = ensure_packages_js(args.packages)
        if pkg_errors:
            return {"error": "Package install failed", "details": pkg_errors}

    # Execute
    if args.language == "python":
        result = run_python(args.code, args.stdin, timeout, args.allow_fs, args.env)
    else:
        result = run_javascript(args.code, args.stdin, timeout, args.allow_fs, args.allow_net, args.env)

    emit_receipt(result, args.allow_fs, args.allow_net)
    return result

# ── Package installer ─────────────────────────────────────────────────────────

def ensure_packages_python(packages: list[str]) -> list[str]:
    errors = []
    PKG_CACHE.mkdir(parents=True, exist_ok=True)
    for pkg in packages:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg,
                 "--quiet", "--break-system-packages"],
                timeout=60, capture_output=True, check=True
            )
        except subprocess.CalledProcessError as e:
            errors.append(f"Failed to install {pkg}: {e.stderr.decode()[:200]}")
    return errors


def ensure_packages_js(packages: list[str]) -> list[str]:
    errors = []
    PKG_CACHE.mkdir(parents=True, exist_ok=True)
    for pkg in packages:
        try:
            subprocess.run(
                ["npm", "install", "-g", pkg, "--quiet"],
                timeout=60, capture_output=True, check=True,
                cwd=str(PKG_CACHE)
            )
        except subprocess.CalledProcessError as e:
            errors.append(f"Failed to install {pkg}: {e.stderr.decode()[:200]}")
    return errors

# ── Execution ─────────────────────────────────────────────────────────────────

def run_python(code: str, stdin_data: str, timeout: int, allow_fs: bool, env_extra: dict) -> dict:
    if allow_fs:
        wrapped = code
    else:
        wrapped = PYTHON_SANDBOX_WRAPPER.format(code=code)

    env = {**os.environ, **env_extra}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(wrapped)
        tmp_path = f.name

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            input=stdin_data.encode() if stdin_data else None,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        elapsed = time.monotonic() - start
        stdout = proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        exit_code = proc.returncode
        truncated = len(proc.stdout) > MAX_OUTPUT_BYTES or len(proc.stderr) > MAX_OUTPUT_BYTES
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        stdout = stderr = ""
        exit_code = -1
        timed_out = True
        truncated = False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return {
        "language": "python",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_sec": round(elapsed, 3),
        "timed_out": timed_out,
        "truncated": truncated,
    }


def run_javascript(code: str, stdin_data: str, timeout: int, allow_fs: bool, allow_net: bool, env_extra: dict) -> dict:
    if allow_fs or allow_net:
        wrapped = JS_SANDBOX_WRAPPER_FS.format(code=code)
    else:
        wrapped = JS_SANDBOX_WRAPPER.format(code=code)

    env = {**os.environ, **env_extra}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(wrapped)
        tmp_path = f.name

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            ["node", "--disallow-code-generation-from-strings", tmp_path],
            input=stdin_data.encode() if stdin_data else None,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        elapsed = time.monotonic() - start
        stdout = proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        exit_code = proc.returncode
        truncated = len(proc.stdout) > MAX_OUTPUT_BYTES or len(proc.stderr) > MAX_OUTPUT_BYTES
    except FileNotFoundError:
        return {"language": "javascript", "exit_code": 127, "stdout": "", "stderr": "node not found on PATH", "elapsed_sec": 0.0, "timed_out": False, "truncated": False}
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        stdout = stderr = ""
        exit_code = -1
        timed_out = True
        truncated = False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return {
        "language": "javascript",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_sec": round(elapsed, 3),
        "timed_out": timed_out,
        "truncated": truncated,
    }

# ── Medulla emit ──────────────────────────────────────────────────────────────

def emit_receipt(result: dict, allow_fs: bool, allow_net: bool) -> None:
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "receipt.code_run",
        "params": {
            "language": result["language"],
            "exit_code": result["exit_code"],
            "elapsed_sec": result["elapsed_sec"],
            "stdout_bytes": len(result["stdout"].encode()),
            "sandbox": {"allow_fs": allow_fs, "allow_net": allow_net},
        }
    })
    try:
        with socket.create_connection((MEDULLA_HOST, MEDULLA_PORT), timeout=1) as sock:
            sock.sendall((payload + "\n").encode())
    except Exception:
        pass

# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Echo code.run skill")
    parser.add_argument("--language", choices=["python", "javascript"], required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--stdin", default="")
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--allow-fs", action="store_true", default=False)
    parser.add_argument("--allow-net", action="store_true", default=False)
    parser.add_argument("--packages", nargs="*", default=[])
    parser.add_argument("--env", type=json.loads, default="{}")
    return parser.parse_args()
