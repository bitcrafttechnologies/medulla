import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from skills.registry import SkillRegistry

BASE_DIR         = Path(__file__).parent
TELEMETRY_CONFIG = BASE_DIR / "telemetry.json"

class TelemetryEngine:
    """
    Runs autonomously. Does not respond to requests. Emits one JSON line
    per event to the configured stream_output file. Two modes per entry:

      interval  — emit every interval_s seconds unconditionally
      event     — evaluate trigger lambda each cycle; emit only when True

    Trigger lambda receives the skill output dict as `data`:
        "trigger": "lambda data: data['level'] < 20"

    Delivery is fire-and-forget. If the stream output file is unavailable,
    the emission is logged and dropped — Medulla does not queue or retry.
    """

    def __init__(
        self,
        registry:      SkillRegistry,
        stream_output: Path,
        log:           logging.Logger,
    ):
        self.registry      = registry
        self.stream_output = stream_output
        self.log           = log
        self.configs       = self._load_config()
        self._tasks: list[asyncio.Task] = []

    def _load_config(self) -> list[dict]:
        if not TELEMETRY_CONFIG.exists():
            self.log.info("No telemetry.json found — telemetry disabled")
            return []
        try:
            configs = json.loads(TELEMETRY_CONFIG.read_text())
            self.log.info("Loaded %d telemetry entries", len(configs))
            return configs
        except Exception as exc:
            self.log.error("Failed to load telemetry.json: %s", exc)
            return []

    def _compile_trigger(self, expr: str):
        """
        Compile a trigger lambda string. expr is author-controlled config,
        not user input — eval is intentional here.
        Must be a pure lambda: no side effects, no I/O, no imports.
        """
        try:
            fn = eval(expr)   # noqa: S307 — config-controlled, not user input
            if not callable(fn):
                raise ValueError("Trigger must evaluate to a callable")
            return fn
        except Exception as exc:
            self.log.error("Bad trigger expression '%s': %s", expr, exc)
            return None

    def start(self) -> None:
        for cfg in self.configs:
            task = asyncio.create_task(self._run_entry(cfg))
            self._tasks.append(task)
        self.log.info("Telemetry engine started — %d active sources", len(self._tasks))

    async def _run_entry(self, cfg: dict) -> None:
        skill_key  = cfg.get("skill")
        mode       = cfg.get("mode", "interval")
        interval_s = cfg.get("interval_s", 10)
        trigger_fn = None

        if mode == "event":
            expr = cfg.get("trigger")
            if not expr:
                self.log.error(
                    "Telemetry entry '%s' is mode=event but has no trigger", skill_key
                )
                return
            trigger_fn = self._compile_trigger(expr)
            if trigger_fn is None:
                return

        self.log.debug(
            "Telemetry loop starting: skill=%s mode=%s interval=%ss",
            skill_key, mode, interval_s,
        )

        while True:
            await asyncio.sleep(interval_s)
            try:
                data = await self.registry.run_skill(skill_key, {})
            except Exception as exc:
                self.log.warning("Telemetry skill '%s' failed: %s", skill_key, exc)
                continue

            should_emit = True
            if mode == "event":
                try:
                    should_emit = bool(trigger_fn(data))
                except Exception as exc:
                    self.log.warning(
                        "Trigger eval failed for '%s': %s", skill_key, exc
                    )
                    should_emit = False

            if should_emit:
                self._emit(skill_key, data)

    def _emit(self, skill_key: str, data: dict) -> None:
        """Append one JSON line to stream_output. Fire-and-forget."""
        payload = {
            "ts":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "telemetry",
            "skill": skill_key,
            "data":  data,
        }
        line = json.dumps(payload) + "\n"
        try:
            self.stream_output.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stream_output, "a", encoding="utf-8") as f:
                f.write(line)
            self.log.info(
                "Telemetry emit",
                extra={"event": "telemetry", "skill": skill_key, "data": data},
            )
        except Exception as exc:
            self.log.error(
                "Failed to write telemetry for '%s': %s", skill_key, exc
            )

    def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
