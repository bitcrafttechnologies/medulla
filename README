> [!WARNING]
This code allows an llm to execute code directly on your computer. If a script is generated and pushes through there are not security constraints. This was made for a specifc acedemic project in mind and has not been tested with cloud ai. It has been tested by ME pushing through code to test. The dumbass, respectfully, on the other side of the screen you're reading this on testing it. Skills like `shell/exec` and `code/run` have real consequences. The included sandbox model reduces risk but does not eliminate it. 
Run this in a controlled environment. If you choose to use this code, use it with caution. You have been warned.
Thank you,
Tucker

---

# Medulla

A lightweight Unix socket daemon that gives a local AI runtime a clean interface to the host machine. Medulla handles skill dispatch, autonomous telemetry, hot-reload, and structured logging — without knowing or caring what's on the other end of the socket.

Built as the system interface layer for a local AI companion targeting embedded and edge hardware (think robots, local inference boxes, and personal AI rigs). Medulla itself has no AI in it — it just executes what it's told and emits what it's configured to emit.

---

## How It Works

Medulla binds a Unix domain socket and waits for JSON requests. Callers send an action, Medulla routes it to the right skill module, and sends back a result. In parallel, a telemetry engine runs configured skill calls on a schedule and appends output to a stream file.

```
Caller ──JSON──► Unix Socket ──► Medulla ──► Skill Module
                                    │
                              Telemetry Engine ──► ~/Your Workspace/telemetry.log
```

Skills are two-file bundles — a Python module (`skill.py`) and a spec file (`skill.md`). Drop a new folder in `skills/` and Medulla picks it up automatically, no restart needed.

---

## Installation

**Requirements:** Python 3.11+, pip

```bash
cd src/medulla
pip install -r requirements.txt
```

That's it. No database, no broker, no external services.

---

## Configuration

Edit `medulla.json` in this directory:

```json
{
  "socket_path":   "~/your_configured_location/medulla.sock",
  "log_path":      "~/your_configured_location/medulla.log",
  "log_max_days":  3,
  "stream_output": "~/your_configured_location/telemetry.sp",
  "skill_workers": 4
}
```

| Field | Description |
|---|---|
| `socket_path` | Path to the Unix socket. Clients connect here. |
| `log_path` | Structured JSON log file. Rotated automatically at midnight. |
| `log_max_days` | How many days of log entries to retain. |
| `stream_output` | File where telemetry emissions are appended. |
| `skill_workers` | Max concurrent skill executions. |

Paths support `~` expansion. All directories are created automatically at startup.

---

## Starting the Daemon

```bash
python3 medulla.py
```

Medulla will:
1. Initialize the workspace runtime directory
2. Prune old log entries
3. Discover and load all skills
4. Bind the Unix socket
5. Start the telemetry engine
6. Start the skill watchdog (live reload)

Stop with `Ctrl-C` or `SIGTERM`. Medulla shuts down cleanly and removes the socket file.

---

## CLI

A dev tool for testing the daemon directly. Reads the socket path from `medulla.json` automatically.

```bash
# Check if Medulla is running and get uptime
python3 medulla_cli.py ping

# List all loaded skills with version and timeout
python3 medulla_cli.py list

# Run a skill
python3 medulla_cli.py run system/battery
python3 medulla_cli.py run web/search '{"mode":"search","query":"llama.cpp apple silicon"}'

# View the structured log (all entries)
python3 medulla_cli.py log

# View log entries since a timestamp
python3 medulla_cli.py log --since 2026-04-14T09:00:00Z

# Stream all output from the socket (stays open)
python3 medulla_cli.py listen
```

---

## Skills

### Bundled Skills

| Skill | Description | Status |
|---|---|---|
| `system/battery` | Battery level and charging state. macOS and Linux. | ready |
| `system/monitor` | CPU, memory, and disk usage. | pending |
| `system/search` | Search the filesystem by name or content. | pending |
| `system/fs/read` | Read a file from the local filesystem. | pending |
| `system/fs/write` | Write content to a file. | pending |
| `system/shell/exec` | Run a shell command and capture output. | ready |
| `system/clipboard/read` | Read the current clipboard contents. | pending |
| `system/clipboard/write` | Write text to the clipboard. | pending |
| `system/notification/send` | Send a desktop notification. | pending |
| `system/calendar/read` | Read upcoming calendar events. | pending |
| `system/window/context` | Get the title and app of the active window. | pending |
| `system/diff/compare` | Diff two text strings or files. | pending |
| `code/run` | Execute sandboxed Python or JavaScript and capture output. | ready |
| `web/search` | DuckDuckGo search and whitelisted URL fetch. No API key needed. | ready |

### Adding a Skill

Create a folder under `skills/<category>/<name>/` with two files:

**`skill.md`** — metadata Medulla reads at load time:
```
# skill: category/name

version:     1.0.0
description: One-line description of what this skill does.
timeout_ms:  5000
```

**`skill.py`** — one required function:
```python
def run(params: dict) -> dict:
    # params comes from the caller's request
    # return any JSON-serializable dict
    return {"result": "ok"}
```

Medulla detects the new files automatically via the watchdog — no restart needed. Skills are addressed by their folder path: `category/name`.

### Telemetry

Configure autonomous skill calls in `telemetry.json`. Two modes:

**`interval`** — emit on a fixed schedule:
```json
{
  "skill": "system/battery",
  "mode": "interval",
  "interval_s": 30
}
```

**`event`** — emit only when a condition is met:
```json
{
  "skill": "system/battery",
  "mode": "event",
  "interval_s": 60,
  "trigger": "lambda data: data['level'] < 20 and not data['charging']"
}
```

Output goes to the file at `stream_output` in `medulla.json`, one JSON line per emission.

---

## Wire Format

All messages are newline-delimited JSON over the Unix socket.

**Request:**
```json
{
  "request_id": "abc-123",
  "action": "run_skill",
  "skill": "system/battery",
  "params": {}
}
```

**Ack** (sent immediately before execution so the caller knows its timeout budget):
```json
{
  "request_id": "abc-123",
  "type": "ack",
  "skill": "system/battery",
  "timeout_ms": 5000
}
```

**Result:**
```json
{
  "request_id": "abc-123",
  "type": "result",
  "status": "ok",
  "data": { "level": 72, "charging": false, "status": "Discharging" },
  "error": null
}
```

**Other actions:** `ping`, `list_skills`, `get_log` (with optional `"since": "<ISO timestamp>"`).

---

## Logging

Logs are written to `log_path` as newline-delimited JSON. Each entry:

```json
{
  "ts": "2026-04-14T09:23:11Z",
  "level": "INFO",
  "component": "medulla",
  "event": "Skill executed",
  "skill": "system/battery",
  "request_id": "abc-123"
}
```

Entries older than `log_max_days` are pruned at startup and at midnight.

---

## Security Notes

- **`shell/exec`** runs commands as the current user with no sandboxing. Only expose Medulla to trusted callers.
- **`code/run`** restricts filesystem and network access by default. Both can be enabled per-call — require confirmation when they are.
- **`web/search` fetch mode** is gated by `skills/web/search/whitelist.json`. Edit that file to control which domains can be fetched.
- The Unix socket is only accessible to processes running as the same user. Do not expose it over a network interface.
