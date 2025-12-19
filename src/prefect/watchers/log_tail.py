from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Iterable, Pattern


@dataclass(frozen=True)
class LogLine:
    ts: float
    line: str


class RollingLogBuffer:
    def __init__(self, *, max_lines: int = 2000):
        self._lines: Deque[LogLine] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line:
            return
        entry = LogLine(ts=time.time(), line=line)
        with self._lock:
            self._lines.append(entry)

    def extend(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.append(line)

    def get_recent(self, n: int) -> list[str]:
        if n <= 0:
            return []
        with self._lock:
            return [e.line for e in list(self._lines)[-n:]]

    def get_since(self, ts: float) -> list[str]:
        with self._lock:
            return [e.line for e in self._lines if e.ts >= ts]

    def get_since_with_ts(self, ts: float) -> list[tuple[float, str]]:
        with self._lock:
            return [(e.ts, e.line) for e in self._lines if e.ts >= ts]

    def search(self, pattern: str, *, n: int = 50, flags: int = re.IGNORECASE) -> list[str]:
        if n <= 0:
            return []
        compiled: Pattern[str] = re.compile(pattern, flags=flags)
        matches: list[str] = []
        with self._lock:
            for entry in reversed(self._lines):
                if compiled.search(entry.line):
                    matches.append(entry.line)
                    if len(matches) >= n:
                        break
        matches.reverse()
        return matches


class StdoutReader:
    """Reads lines from a text-mode file-like object and writes to a RollingLogBuffer."""

    def __init__(self, stream, buffer: RollingLogBuffer, *, on_line=None):
        self._stream = stream
        self._buffer = buffer
        self._on_line = on_line
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="prefect-stdout-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            line = self._stream.readline()
            if not line:
                time.sleep(0.05)
                continue
            if self._on_line is not None:
                try:
                    self._on_line(line)
                except Exception:
                    # Never let parsing break log capture.
                    pass
            self._buffer.append(line)


class FileTailer:
    """Tails a file and writes new lines into a RollingLogBuffer."""

    def __init__(
        self, 
        path: Path, 
        buffer: RollingLogBuffer, 
        *, 
        poll_interval: float = 0.2,
        on_line: Callable[[str], None] | None = None,
    ):
        self._path = path
        self._buffer = buffer
        self._poll_interval = poll_interval
        self._on_line = on_line
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="prefect-file-tailer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Best-effort tail: reopen if file rotates.
        last_inode: int | None = None
        fh = None
        try:
            while not self._stop.is_set():
                try:
                    stat = self._path.stat()
                    inode = stat.st_ino
                    if fh is None or inode != last_inode:
                        if fh is not None:
                            fh.close()
                        fh = self._path.open("r", encoding="utf-8", errors="replace")
                        fh.seek(0, 2)  # end
                        last_inode = inode

                    line = fh.readline()
                    if line:
                        self._buffer.append(line)
                        # Call the on_line callback if provided (for chat parsing, etc.)
                        if self._on_line is not None:
                            try:
                                self._on_line(line)
                            except Exception:
                                pass  # Don't let callback errors stop tailing
                        continue

                except FileNotFoundError:
                    pass

                time.sleep(self._poll_interval)
        finally:
            if fh is not None:
                fh.close()
