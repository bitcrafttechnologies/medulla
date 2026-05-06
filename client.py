import socket
from pathlib import Path
import json
import uuid

def _rid() -> str:
    return str(uuid.uuid4())

# ---------------------------------------------------------------------------
# Medulla Unix socket client
# ---------------------------------------------------------------------------

class MedullaClient:
    """
    Synchronous client for the Medulla Unix domain socket.
    Wire format: newline-delimited JSON (one request → one or two responses).

    run_skill returns two messages: ack (type=ack) then result (type=result).
    All other actions return a single result message.
    We read until we see type=result and return it.
    """

    def __init__(self, socket_path: Path) -> None:
        self._path = socket_path

    def _call(self, payload: dict, timeout: float = 15.0) -> dict:
        """Send one request; return the first type=result response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self._path))
            sock.sendall((json.dumps(payload) + "\n").encode())
            buf = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line.decode())
                    if msg.get("type") == "result":
                        return msg
                    # type=ack — keep reading for the result
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return {"type": "result", "status": "error",
                "error": "connection closed before result"}

    def ping(self) -> dict:
        return self._call({"action": "ping", "request_id": _rid()})

    def list_skills(self) -> list[dict]:
        r = self._call({"action": "list_skills", "request_id": _rid()})
        return r.get("data") or []

    def run_skill(self, skill_key: str, params: dict) -> dict:
        r = self._call({
            "action":     "run_skill",
            "skill":      skill_key,
            "params":     params,
            "request_id": _rid(),
        })
        if r.get("status") == "error":
            raise RuntimeError(r.get("error") or f"skill '{skill_key}' failed")
        return r.get("data") or {}

    def available(self) -> bool:
        try:
            self.ping()
            return True
        except Exception:
            return False