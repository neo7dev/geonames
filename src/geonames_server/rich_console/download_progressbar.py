from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


class DownloadProgressbar:
    """Small wrapper around `rich.progress.Progress` to manage a single task.

    Usage:
        with Progressbar() as pb:
            task = pb.add(f"Downloading ...", total=total)
            pb.update(advance=123)
            pb.update(completed=existing)
    """

    def __init__(self) -> None:
        self._progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        )
        self._task_id = None

    def __enter__(self) -> "DownloadProgressbar":
        self._progress.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return self._progress.__exit__(exc_type, exc, tb)

    def add(self, description: str, total: int | None = None, completed: int = 0):
        self._task_id = self._progress.add_task(description, total=total)
        if completed:
            self._progress.update(self._task_id, completed=completed)
        return self._task_id

    def update(self, advance: int = 0, completed: int | None = None) -> None:
        if self._task_id is None:
            return
        if completed is not None:
            self._progress.update(self._task_id, completed=completed)
        elif advance:
            self._progress.update(self._task_id, advance=advance)
