"""Terminal screen state and ring buffer management."""

from collections import deque

import pyte


class VirtualScreen:
    """Maintains a 2D virtual terminal screen state using pyte."""

    def __init__(self, cols: int = 80, rows: int = 24, history: int = 1000):
        self.cols = cols
        self.rows = rows
        self.screen = pyte.HistoryScreen(cols, rows, history=history)
        self.stream = pyte.Stream(self.screen)

    def feed(self, data: bytes) -> None:
        """Feeds raw byte output from PTY into the terminal emulator."""
        try:
            text = data.decode("utf-8", errors="replace")
            self.stream.feed(text)
        except Exception:
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Resizes the virtual screen dimensions."""
        self.rows = rows
        self.cols = cols
        self.screen.resize(lines=rows, columns=cols)

    def snapshot(self, clean: bool = True) -> str:
        """Returns the current rendered screen lines as a single string."""
        lines = [line for line in self.screen.display]
        if clean:
            # Strip trailing spaces on each line
            lines = [line.rstrip() for line in lines]
            # Strip trailing blank lines
            while lines and not lines[-1]:
                lines.pop()
        return "\n".join(lines)


class RingBuffer:
    """Thread-safe and memory-bounded ring buffer for recent log output."""

    def __init__(self, max_lines: int = 10000, max_bytes: int = 5 * 1024 * 1024):
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self._raw_chunks = deque()
        self._total_bytes = 0

    def append(self, data: bytes) -> None:
        self._raw_chunks.append(data)
        self._total_bytes += len(data)
        while self._total_bytes > self.max_bytes and len(self._raw_chunks) > 1:
            popped = self._raw_chunks.popleft()
            self._total_bytes -= len(popped)

    def get_raw_bytes(self) -> bytes:
        return b"".join(self._raw_chunks)

    def get_lines(self, num_lines: int = 100) -> list[str]:
        raw = self.get_raw_bytes()
        text = raw.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        if num_lines <= 0:
            return all_lines
        return all_lines[-num_lines:]
