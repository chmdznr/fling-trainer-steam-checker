"""Progress/log reporting abstraction so core logic is UI-agnostic.

CLI uses :class:`CliReporter` (tqdm-backed, identical to old behavior).
The TUI posts events from a worker thread via its own reporter.
Tests use :class:`NullReporter` or a small recording fake.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from fling_checker.config import print as _print


class Reporter(Protocol):
    """Event sink for long-running pipeline stages."""

    def phase(self, name: str) -> None:
        ...

    def progress(self, desc: str, done: int, total: int | None) -> None:
        ...

    def log(self, msg: str) -> None:
        ...

    def item(self, result: dict) -> None:
        ...

    def is_cancelled(self) -> bool:
        ...


class NullReporter:
    """No-op reporter for tests and headless use."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def phase(self, name: str) -> None:
        pass

    def progress(self, desc: str, done: int, total: int | None) -> None:
        pass

    def log(self, msg: str) -> None:
        pass

    def item(self, result: dict) -> None:
        pass

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()


class CliReporter:
    """tqdm-backed reporter preserving the original CLI output."""

    def __init__(self) -> None:
        from tqdm import tqdm

        self._tqdm = tqdm
        self._bars: dict[str, Any] = {}
        self._cancelled = threading.Event()

    def phase(self, name: str) -> None:
        pass

    def _bar(self, desc: str, total: int | None):
        bar = self._bars.get(desc)
        if bar is None:
            bar = self._tqdm(desc=desc, unit="page" if "FLiNG" in desc else "game", total=total)
            self._bars[desc] = bar
        return bar

    def progress(self, desc: str, done: int, total: int | None) -> None:
        bar = self._bar(desc, total)
        if total is None:
            bar.set_postfix_str(f"page {done}")
            bar.update(1)
        else:
            bar.total = total
            bar.n = done
            bar.refresh()

    def log(self, msg: str) -> None:
        self._tqdm.write(msg)

    def item(self, result: dict) -> None:
        pass

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def close(self) -> None:
        for bar in self._bars.values():
            bar.close()
        self._bars.clear()


def coerce_reporter(reporter: Reporter | None) -> Reporter | None:
    """Return the reporter unchanged; None means legacy direct output."""
    return reporter


def say(reporter: Reporter | None, msg: str) -> None:
    """Emit a status line: stdout in CLI mode, event log in UI mode.

    Only :data:`None` (legacy direct output) prints to stdout. Every
    reporter object — including :class:`CliReporter` — receives the
    message via :meth:`log`, so TUI/tests never touch stdout.
    """
    if reporter is None:
        _print(msg)
    else:
        reporter.log(msg)
