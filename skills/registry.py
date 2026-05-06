import asyncio
import importlib
import logging
from pathlib import Path
import re
import importlib.util

# ---------------------------------------------------------------------------
# skill.md parser — extracts version, description, timeout_ms
# ---------------------------------------------------------------------------

def parse_skill_md(md_path: Path) -> dict:
    """
    Parse a skill.md file for machine-readable fields.
    Returns a dict with 'version', 'description', 'timeout_ms'.
    Missing fields fall back to safe defaults.
    """
    text     = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    metadata = {"version": "0.0.0", "description": "(no description)", "timeout_ms": 5000}
    for line in text.splitlines():
        m = re.match(r"^(version|description|timeout_ms)\s*:\s*(.+)$", line.strip())
        if m:
            key, val = m.group(1), m.group(2).strip()
            metadata[key] = int(val) if key == "timeout_ms" else val
    return metadata

class SkillRegistry:
    """
    Discovers and loads skill modules from skills/<category>/<name>/.
    Each skill exposes:
        skill.py  — run(params: dict) -> dict
        skill.md  — version, description, timeout_ms (parsed by Medulla)

    Skills are addressed as "<category>/<name>", e.g. "system/battery".
    Watchdog triggers hot-reload when skill files change on disk.
    In-flight executions are never interrupted — reload takes effect for
    subsequent requests only.
    """

    def __init__(self, skills_dir: Path, max_workers: int = 4):
        self.skills_dir = skills_dir
        self._max_workers = max_workers
        self._skills: dict[str, dict] = {}
        self._executor = None   # created lazily in async context
        self._lock = asyncio.Lock()
        self.discover()

    def discover(self) -> None:
        found = 0
        for py_path in self.skills_dir.rglob("skill.py"):
            parts = py_path.parent.relative_to(self.skills_dir).parts
            if len(parts) < 2:
                logging.getLogger("medulla").warning(
                    "Skipping unexpected skill path depth: %s", py_path
                )
                continue
            key = "/".join(parts)
            self._load(key, py_path)
            found += 1
        logging.getLogger("medulla").info(
            "Skill discovery complete — %d skills loaded", found
        )

    def _load(self, key: str, py_path: Path) -> None:
        md_path  = py_path.with_name("skill.md")
        metadata = parse_skill_md(md_path)

        module_name = f"skill_{key.replace('/', '_')}"
        spec        = importlib.util.spec_from_file_location(module_name, py_path)
        module      = importlib.util.module_from_spec(spec)

        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logging.getLogger("medulla").error(
                "Failed to load skill '%s': %s", key, exc, exc_info=True
            )
            return

        if not callable(getattr(module, "run", None)):
            logging.getLogger("medulla").error(
                "Skill '%s' missing callable run(params) — skipped", key
            )
            return

        self._skills[key] = {
            "key":         key,
            "module":      module,
            "version":     metadata["version"],
            "description": metadata["description"],
            "timeout_ms":  metadata["timeout_ms"],
            "path":        str(py_path),
        }
        logging.getLogger("medulla").debug("Loaded skill: %s (v%s)", key, metadata["version"])

    def reload(self, py_path: Path) -> None:
        parts = py_path.parent.relative_to(self.skills_dir).parts
        if len(parts) < 2:
            return
        key = "/".join(parts)
        logging.getLogger("medulla").info("Reloading skill: %s", key)
        self._load(key, py_path)

    def get(self, key: str) -> dict | None:
        return self._skills.get(key)

    async def run_skill(self, key: str, params: dict) -> dict:
        """Execute skill.run() in a thread pool executor (non-blocking)."""
        async with self._lock:
            entry = self._skills.get(key)
        if entry is None:
            raise KeyError(f"Unknown skill: '{key}'")
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, entry["module"].run, params)
        return result

    def list_skills(self) -> list[dict]:
        return [
            {
                "skill":       e["key"],
                "version":     e["version"],
                "description": e["description"],
                "timeout_ms":  e["timeout_ms"],
            }
            for e in self._skills.values()
        ]
