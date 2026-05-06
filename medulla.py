"""
medulla.py — Echo System Interface Daemon
Bitcraft Technologies · Echo Project

Receives JSON requests from Echo Core over a Unix domain socket,
routes them to skill modules, emits autonomous telemetry to a
configured stream output file, and maintains a structured rolling log.

Startup sequence (per architecture spec):
  1. Medulla         ← this process
  2. MetaDB          ← started after socket is available
  3. Echo Core       ← started after MetaDB is ready
  4. Scratchpad      ← initialized by Echo Core at session start

Medulla is domain-agnostic. It has no knowledge of Echo's reasoning,
memory, or session state. It executes what it is asked, emits what it
is configured to emit, and stays out of everything else.
"""

import asyncio
import json
import logging
import re
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from watchdog.observers import Observer

from skills.registry import SkillRegistry
from skills.watcher import SkillWatcher
from telemetry import TelemetryEngine

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR         = Path(__file__).parent
CONFIG_PATH      = BASE_DIR / "medulla.json"
SKILLS_DIR       = BASE_DIR / "skills"

def load_config(path: Path) -> dict:
    """
    Read medulla.json and expand ~ in all path fields.
    Falls back to safe defaults if the file is missing or malformed.
    """
    defaults = {
        "socket_path":   str(Path.home() / ".echospace" / "medulla.sock"),
        "log_path":      str(Path.home() / ".echospace" / "medulla.log"),
        "log_max_days":  3,
        "stream_output": str(Path.home() / ".echospace" / "telemetry.sp"),
        "skill_workers": 4,
    }
    if not path.exists():
        return defaults
    try:
        cfg = json.loads(path.read_text())
        for key in ("socket_path", "log_path", "stream_output"):
            if key in cfg:
                cfg[key] = str(Path(cfg[key]).expanduser())
        return {**defaults, **cfg}
    except Exception:
        return defaults


CONFIG = load_config(CONFIG_PATH)
SOCKET_PATH   = Path(CONFIG["socket_path"])
LOG_PATH      = Path(CONFIG["log_path"])
STREAM_OUTPUT = Path(CONFIG["stream_output"])
SKILL_WORKERS = int(CONFIG["skill_workers"])
LOG_MAX_DAYS  = int(CONFIG["log_max_days"])


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """
    Formats every log record as a single JSON line.
    Telemetry events carry a 'data' field; skill errors carry 'traceback'.
    """
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level":     record.levelname,
            "component": record.name,
            "event":     record.getMessage(),
        }
        for field in ("skill", "data", "error", "request_id"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = _JsonFormatter()

    file_handler   = logging.FileHandler(log_path)
    stderr_handler = logging.StreamHandler(sys.stderr)
    for h in (file_handler, stderr_handler):
        h.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
    return logging.getLogger("medulla")


# ---------------------------------------------------------------------------
# Log rotation — prune entries older than log_max_days
# ---------------------------------------------------------------------------

def prune_log(log_path: Path, max_days: int) -> None:
    """
    Rewrite the log file in place, dropping entries older than max_days.
    Runs at startup and is scheduled again at each midnight.
    Entries that cannot be parsed are kept (conservative).
    """
    if not log_path.exists():
        return
    cutoff   = datetime.now(timezone.utc) - timedelta(days=max_days)
    raw      = log_path.read_text(encoding="utf-8", errors="replace")
    kept     = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts    = datetime.strptime(entry["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                kept.append(line)
        except Exception:
            kept.append(line)  # keep anything we can't parse
    log_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


async def _schedule_midnight_prune(log_path: Path, max_days: int) -> None:
    """Sleep until the next midnight UTC, prune, then repeat."""
    while True:
        now     = datetime.now(timezone.utc)
        next_mn = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_mn - now).total_seconds())
        prune_log(log_path, max_days)
        logging.getLogger("medulla").info("Log pruned at midnight")




# ---------------------------------------------------------------------------
# Request / response helpers
# ---------------------------------------------------------------------------

def _result(request_id: str, data: Any = None, error: str | None = None) -> bytes:
    payload = {
        "request_id": request_id,
        "type":       "result",
        "status":     "error" if error else "ok",
        "data":       data,
        "error":      error,
    }
    return (json.dumps(payload) + "\n").encode()


def _ack(request_id: str, skill_key: str, timeout_ms: int) -> bytes:
    payload = {
        "request_id": request_id,
        "type":       "ack",
        "skill":      skill_key,
        "timeout_ms": timeout_ms,
    }
    return (json.dumps(payload) + "\n").encode()


# ---------------------------------------------------------------------------
# Client connection handler
# ---------------------------------------------------------------------------

async def client_handler(
    reader:    asyncio.StreamReader,
    writer:    asyncio.StreamWriter,
    registry:  SkillRegistry,
    start_time: float,
    log:       logging.Logger,
) -> None:
    """
    Handles one client connection. Connection type is determined by the
    first action received:
      - run_skill / list_skills / ping / get_log → request connection
      - timeout_ms: 0 in ack → stream connection (telemetry, stays open)
    """
    log.debug("Client connected")
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            raw = line.decode(errors="replace").strip()
            if not raw:
                continue

            try:
                req = json.loads(raw)
            except json.JSONDecodeError as exc:
                writer.write(_result(
                    str(uuid.uuid4()), error=f"JSON parse error: {exc}"
                ))
                await writer.drain()
                continue

            request_id = req.get("request_id") or str(uuid.uuid4())
            action     = req.get("action")
            log.debug("Request [%s] action=%s", request_id, action)

            # ── ping ──────────────────────────────────────────────────────
            if action == "ping":
                uptime = int(time.monotonic() - start_time)
                writer.write(_result(request_id, data={"uptime_s": uptime}))
                await writer.drain()
                continue

            # ── list_skills ───────────────────────────────────────────────
            if action == "list_skills":
                writer.write(_result(request_id, data=registry.list_skills()))
                await writer.drain()
                continue

            # ── get_log ───────────────────────────────────────────────────
            if action == "get_log":
                since_raw = req.get("since")
                entries   = _read_log_since(LOG_PATH, since_raw, log)
                writer.write(_result(request_id, data=entries))
                await writer.drain()
                continue

            # ── run_skill ─────────────────────────────────────────────────
            if action == "run_skill":
                skill_key = req.get("skill")
                params    = req.get("params") or {}

                if not skill_key:
                    writer.write(_result(
                        request_id, error="'skill' field required for run_skill"
                    ))
                    await writer.drain()
                    continue

                entry = registry.get(skill_key)
                if entry is None:
                    writer.write(_result(
                        request_id, error=f"Unknown skill: '{skill_key}'"
                    ))
                    await writer.drain()
                    continue

                # Send ack before executing — tells caller the timeout budget
                writer.write(_ack(request_id, skill_key, entry["timeout_ms"]))
                await writer.drain()

                try:
                    data  = await registry.run_skill(skill_key, params)
                    log.info(
                        "Skill executed",
                        extra={"event": "skill_result", "skill": skill_key,
                               "request_id": request_id}
                    )
                    writer.write(_result(request_id, data=data))
                except Exception as exc:
                    log.error(
                        "Skill error: %s", exc,
                        extra={"event": "skill_error", "skill": skill_key,
                               "request_id": request_id},
                        exc_info=True,
                    )
                    writer.write(_result(request_id, error=str(exc)))
                await writer.drain()
                continue

            # ── unknown ───────────────────────────────────────────────────
            writer.write(_result(request_id, error=f"Unknown action: '{action}'"))
            await writer.drain()

    except asyncio.IncompleteReadError:
        pass
    except Exception as exc:
        log.error("Client handler error: %s", exc, exc_info=True)
    finally:
        log.debug("Client disconnected")
        try:
            writer.close()
        except Exception:
            pass


def _read_log_since(log_path: Path, since_raw: str | None, log: logging.Logger) -> list:
    """
    Parse the structured log file and return entries after `since` timestamp.
    `since_raw` is an ISO 8601 string (e.g. "2026-04-11T09:00:00Z").
    If since_raw is None, returns all entries.
    """
    if not log_path.exists():
        return []

    cutoff = None
    if since_raw:
        try:
            cutoff = datetime.strptime(since_raw.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            log.warning("get_log: unparseable 'since' value: %s", since_raw)

    entries = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if cutoff and "ts" in entry:
                ts = datetime.strptime(entry["ts"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                if ts < cutoff:
                    continue
            entries.append(entry)
        except Exception:
            pass
    return entries

# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class MedullaServer:
    """
    Owns the Unix socket, skill registry, watchdog, and telemetry engine.
    Wires them together and manages the lifecycle.
    """

    def __init__(self, log: logging.Logger):
        self.log        = log
        self.registry   = SkillRegistry(SKILLS_DIR, SKILL_WORKERS)
        self.telemetry  = TelemetryEngine(self.registry, STREAM_OUTPUT, log)
        self._observer  = None
        self._server    = None
        self._start_time = time.monotonic()

    def _start_watchdog(self) -> None:
        handler  = SkillWatcher(self.registry)
        observer = Observer()
        observer.schedule(handler, str(SKILLS_DIR), recursive=True)
        observer.start()
        self._observer = observer
        self.log.info("Watchdog started — watching %s", SKILLS_DIR)

    async def run(self) -> None:
        # Ensure the socket directory exists
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Remove stale socket from a previous run
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        # Prune old log entries at startup
        prune_log(LOG_PATH, LOG_MAX_DAYS)
        self.log.info("Medulla starting — socket=%s", SOCKET_PATH)

        self._start_watchdog()

        # Bind the Unix socket
        self._server = await asyncio.start_unix_server(
            lambda r, w: client_handler(
                r, w, self.registry, self._start_time, self.log
            ),
            path=str(SOCKET_PATH),
        )
        self.log.info("Medulla listening on %s", SOCKET_PATH)

        self.telemetry.start()

        # Schedule midnight log pruning
        asyncio.create_task(
            _schedule_midnight_prune(LOG_PATH, LOG_MAX_DAYS)
        )

        # Graceful shutdown on SIGINT / SIGTERM
        loop       = asyncio.get_event_loop()
        stop_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        self.log.info("Medulla ready")
        await stop_event.wait()
        await self._shutdown()

    async def _shutdown(self) -> None:
        self.log.info("Shutting down Medulla...")
        self.telemetry.stop()
        if self._observer:
            self._observer.stop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        self.log.info("Medulla stopped cleanly")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Initialize EchoSpace before anything else touches the filesystem
    # sys.path.insert(0, str(Path(__file__).parent.parent))
    # from init_echospace import init as init_echospace

    # result = init_echospace()
    # if not result["ok"]:
    #     print("EchoSpace init failed — cannot start Medulla", file=sys.stderr)
    #     sys.exit(1)

    log = _setup_logging(LOG_PATH)
    asyncio.run(MedullaServer(log).run())
