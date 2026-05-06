
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from .registry import SkillRegistry


class SkillWatcher(FileSystemEventHandler):
    """Watches skills/ for file changes and triggers registry reload."""

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith("skill.py"):
            self.registry.reload(Path(event.src_path))

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith("skill.py"):
            self.registry.reload(Path(event.src_path))