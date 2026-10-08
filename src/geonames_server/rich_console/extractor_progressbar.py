from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)


class ExtractorProgressBar:
    """Progress bar for zip extraction using rich.Progress.

    Use as a context manager::

            with ExtractorProgressBar(total=total_bytes, description="Extracting") as pb:
                    pb.update(bytes)

    """

    def __init__(self, total: int, description: str = "Extracting"):
        self.total = int(total) if total is not None else 0
        self.description = description
        self.progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(bar_width=None),
            "{task.percentage:>3.0f}%",
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            transient=False,
        )
        self.task_id = None

    def __enter__(self):
        self.progress.start()
        # If total is 0, create an indeterminate task
        total = self.total if self.total > 0 else None
        self.task_id = self.progress.add_task(self.description, total=total)
        return self

    def update(self, advance: int):
        if self.task_id is not None:
            self.progress.advance(self.task_id, advance)

    def __exit__(self, exc_type, exc, tb):
        try:
            # finish the task so UI shows 100% if we reached total
            if self.task_id is not None:
                self.progress.update(self.task_id, completed=self.total)
        finally:
            self.progress.stop()
