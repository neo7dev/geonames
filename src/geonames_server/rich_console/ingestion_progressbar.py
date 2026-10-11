from pathlib import Path

from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from geonames_server.log.console import ProgressMilestones, console, in_terminal
from geonames_server.log.mylogger import get_logger

log = get_logger()


class IngestionProgressBar:
    """Rich progress bar for ingestion operations.

    Keeps the progress visible after completion by default.
    """

    def __init__(self, total: int, description: str = "Ingesting"):
        self.total = int(total) if total is not None else 0
        self.description = description
        self.progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(bar_width=None),
            "{task.percentage:>3.0f}%",
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            transient=False,
            console=console,
            # Without a terminal it is logged in steps instead (milestones).
            disable=not in_terminal(),
        )
        self.task_id = None
        self.milestones = (
            None
            if in_terminal()
            else ProgressMilestones(log, self.description, self.total)
        )

    def __enter__(self):
        self.progress.start()
        total = self.total if self.total > 0 else None
        self.task_id = self.progress.add_task(self.description, total=total)
        return self

    def update(self, advance: int):
        if self.task_id is not None and advance:
            self.progress.advance(self.task_id, advance)
            if self.milestones:
                self.milestones.advance(advance)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.task_id is not None:
                self.progress.update(self.task_id, completed=self.total)
        finally:
            self.progress.stop()


class _ProgressFileWrapper:
    def __init__(self, path: Path, progress: IngestionProgressBar):
        self._f = open(path, "r", encoding="utf-8", newline="")
        self.progress = progress

    def read(self, size=-1):
        data = self._f.read(size)
        if data:
            self.progress.update(len(data.encode("utf-8")))
        return data

    def close(self):
        self._f.close()
