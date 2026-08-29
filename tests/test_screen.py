from remote_cli.screen import RingBuffer, VirtualScreen
from remote_cli.utils import strip_ansi


def test_virtual_screen_basic():
    screen = VirtualScreen(cols=40, rows=10)
    screen.feed(b"Hello World\r\nLine 2\r\n")
    snapshot = screen.snapshot()
    lines = snapshot.splitlines()
    assert len(lines) >= 2
    assert "Hello World" in lines[0]
    assert "Line 2" in lines[1]


def test_virtual_screen_ansi():
    screen = VirtualScreen(cols=40, rows=10)
    # ANSI green text + clear line
    screen.feed(b"\x1b[32mColored Text\x1b[0m\r\n")
    snapshot = screen.snapshot()
    assert "Colored Text" in snapshot


def test_ring_buffer():
    buf = RingBuffer(max_lines=5, max_bytes=100)
    buf.append(b"Line 1\nLine 2\nLine 3\n")
    lines = buf.get_lines(num_lines=2)
    assert lines == ["Line 2", "Line 3"]


def test_strip_ansi():
    text_with_ansi = "\x1b[1;31mError:\x1b[0m file not found\n"
    clean = strip_ansi(text_with_ansi)
    assert clean == "Error: file not found\n"
