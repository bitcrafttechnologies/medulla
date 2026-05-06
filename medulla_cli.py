#!/usr/bin/env python3
"""
medulla_cli.py — Dev tool for talking to the Medulla Unix socket.

Reads socket path from medulla.json in the same directory, falling back
to ~/.echospace/medulla.sock.

Usage:
  python medulla_cli.py ping
  python medulla_cli.py list
  python medulla_cli.py run system/battery
  python medulla_cli.py run web/search '{"mode":"search","query":"llama.cpp M1"}'
  python medulla_cli.py log
  python medulla_cli.py log --since 2026-04-11T09:00:00Z
  python medulla_cli.py listen
"""

import json
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — resolve socket path from medulla.json
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_CFG_PATH = _HERE / "medulla.json"

def _socket_path() -> str:
    default = str(Path.home() / ".echospace" / "medulla.sock")
    if not _CFG_PATH.exists():
        return default
    try:
        cfg = json.loads(_CFG_PATH.read_text())
        raw = cfg.get("socket_path", default)
        return str(Path(raw).expanduser())
    except Exception:
        return default

SOCKET_PATH = _socket_path()


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _send(payload: dict) -> list[dict]:
    """
    Send one request, collect all response lines until the connection closes
    or we receive a 'result' type message (for request connections).
    Returns a list of parsed response dicts.
    """
    raw = (json.dumps(payload) + "\n").encode()
    responses = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCKET_PATH)
        s.sendall(raw)
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode())
                    responses.append(msg)
                    # Request connections close after the result
                    if msg.get("type") == "result":
                        return responses
                except json.JSONDecodeError:
                    pass
    return responses


def _print_json(obj):
    print(json.dumps(obj, indent=2))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_ping():
    resp = _send({"request_id": str(uuid.uuid4()), "action": "ping"})
    for msg in resp:
        _print_json(msg)


def cmd_list():
    resp = _send({"request_id": str(uuid.uuid4()), "action": "list_skills"})
    for msg in resp:
        if msg.get("status") == "ok" and isinstance(msg.get("data"), list):
            print(f"{'Skill':<25} {'Version':<10} {'Timeout':<10}  Description")
            print("-" * 80)
            for skill in msg["data"]:
                print(
                    f"{skill['skill']:<25} "
                    f"{skill.get('version','?'):<10} "
                    f"{skill.get('timeout_ms','?'):<10}  "
                    f"{skill.get('description','')}"
                )
        else:
            _print_json(msg)


def cmd_run(skill: str, params_raw: str | None):
    params = {}
    if params_raw:
        try:
            params = json.loads(params_raw)
        except json.JSONDecodeError as exc:
            print(f"Error: params must be valid JSON — {exc}", file=sys.stderr)
            sys.exit(1)

    payload = {
        "request_id": str(uuid.uuid4()),
        "action":     "run_skill",
        "skill":      skill,
        "params":     params,
    }
    resp = _send(payload)
    for msg in resp:
        _print_json(msg)


def cmd_log(since: str | None):
    payload = {"request_id": str(uuid.uuid4()), "action": "get_log"}
    if since:
        payload["since"] = since
    resp = _send(payload)
    for msg in resp:
        if msg.get("status") == "ok" and isinstance(msg.get("data"), list):
            entries = msg["data"]
            print(f"{len(entries)} log entries")
            print()
            for entry in entries:
                ts    = entry.get("ts", "?")
                level = entry.get("level", "?")
                event = entry.get("event", "")
                skill = entry.get("skill", "")
                line  = f"[{ts}] {level:<8} {event}"
                if skill:
                    line += f"  skill={skill}"
                print(line)
        else:
            _print_json(msg)


def cmd_listen():
    """Stream all Medulla output indefinitely (Ctrl-C to stop)."""
    print(f"Listening on {SOCKET_PATH}  (Ctrl-C to stop)\n")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCKET_PATH)
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode())
                    _print_json(msg)
                    print()
                except json.JSONDecodeError:
                    print(line.decode())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]

    if cmd == "ping":
        cmd_ping()

    elif cmd == "list":
        cmd_list()

    elif cmd == "run":
        if len(args) < 2:
            print("Usage: medulla_cli.py run <skill> [params_json]", file=sys.stderr)
            sys.exit(1)
        cmd_run(args[1], args[2] if len(args) > 2 else None)

    elif cmd == "log":
        since = None
        if "--since" in args:
            idx = args.index("--since")
            if idx + 1 < len(args):
                since = args[idx + 1]
        cmd_log(since)

    elif cmd == "listen":
        cmd_listen()

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
