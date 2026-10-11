"""The one Rich console that logs, progress bars and debug panels print to.

In a terminal Rich follows its width, resizes included. In a container there is
no terminal: the output is stored by Docker as text and replayed later, so it
is drawn at LOG_WIDTH columns (Rich's fallback would be 80).
"""

import logging
import sys
import time

from rich.console import Console
from rich.theme import Theme

DEFAULT_LOG_WIDTH = 160


def _isatty(file) -> bool:
    try:
        return file.isatty()
    except (AttributeError, ValueError):
        return False


# FORCE_COLOR (set in the container for coloured logs) makes Rich treat any
# output as a terminal, so the checks here ask whether it really is one.
_tty = _isatty(sys.stdout)
console = Console(
    theme=Theme({"header_style": "bold magenta"}),
    # No animation in stored logs: a redrawn line would be stored every time
    force_interactive=_tty,
    width=None if _tty else DEFAULT_LOG_WIDTH,
)


def in_terminal() -> bool:
    """Whether the console writes to a real terminal, which can redraw and resize."""
    return _isatty(console.file)


def set_log_width(width: int) -> None:
    """Draw at `width` columns when not in a terminal; a terminal keeps its own."""
    if not in_terminal():
        console.width = width


def get_console() -> Console:
    return console


class ProgressMilestones:
    """Without a terminal a progress bar cannot redraw in place, so it is not
    drawn: this logs every `step` percent instead, at most once every
    `min_interval` seconds, so a file done in seconds logs nothing."""

    def __init__(
        self,
        logger: logging.Logger,
        description: str,
        total: int | None,
        completed: int = 0,
        unit: str = "MB",
        step: int = 10,
        min_interval: float = 5.0,
    ):
        self._log = logger
        self._description = description
        self._total = total or 0
        self._completed = completed
        self._divisor = 1_000_000 if unit == "MB" else 1
        self._unit = unit
        self._step = step
        self._logged = self._percent() // step * step
        self._min_interval = min_interval
        self._last = time.monotonic()

    def _percent(self) -> int:
        if self._total <= 0:
            return 0
        return min(100, self._completed * 100 // self._total)

    def advance(self, amount: int) -> None:
        self.set(self._completed + amount)

    def set(self, completed: int) -> None:
        self._completed = completed
        percent = self._percent()
        now = time.monotonic()
        if (
            percent >= self._logged + self._step
            and now - self._last >= self._min_interval
        ):
            self._logged = percent // self._step * self._step
            self._last = now
            self._log.info(
                f"{self._description}: {self._logged}% "
                f"({self._completed / self._divisor:,.1f} of "
                f"{self._total / self._divisor:,.1f} {self._unit})"
            )
