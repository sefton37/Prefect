"""Tests for prefect.watchers.log_tail module."""
from __future__ import annotations

import io
import threading
import time

import pytest

from prefect.watchers.log_tail import RollingLogBuffer, StdoutReader, LogLine


class TestRollingLogBuffer:
    """Tests for RollingLogBuffer class."""

    def test_append_single_line(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("test line")
        assert buf.get_recent(1) == ["test line"]

    def test_append_strips_newline(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("test line\n")
        buf.append("another line\n\n")
        lines = buf.get_recent(2)
        assert lines == ["test line", "another line"]

    def test_append_ignores_empty(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("")
        buf.append("\n")
        buf.append("valid")
        assert buf.get_recent(10) == ["valid"]

    def test_extend(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.extend(["line1", "line2", "line3"])
        assert buf.get_recent(10) == ["line1", "line2", "line3"]

    def test_get_recent_n(self):
        buf = RollingLogBuffer(max_lines=100)
        for i in range(10):
            buf.append(f"line{i}")
        
        assert len(buf.get_recent(5)) == 5
        assert buf.get_recent(5) == ["line5", "line6", "line7", "line8", "line9"]
        assert len(buf.get_recent(100)) == 10

    def test_get_recent_zero_or_negative(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("test")
        assert buf.get_recent(0) == []
        assert buf.get_recent(-1) == []

    def test_max_lines_limit(self):
        buf = RollingLogBuffer(max_lines=5)
        for i in range(10):
            buf.append(f"line{i}")
        
        lines = buf.get_recent(10)
        assert len(lines) == 5
        assert lines == ["line5", "line6", "line7", "line8", "line9"]

    def test_get_since(self):
        buf = RollingLogBuffer(max_lines=100)
        t1 = time.time()
        buf.append("early")
        time.sleep(0.01)
        t2 = time.time()
        buf.append("late")
        
        since = buf.get_since(t2)
        assert "late" in since
        assert "early" not in since

    def test_get_since_with_ts(self):
        buf = RollingLogBuffer(max_lines=100)
        t1 = time.time()
        buf.append("early")
        time.sleep(0.01)
        t2 = time.time()
        buf.append("late")
        
        since = buf.get_since_with_ts(t2)
        assert len(since) == 1
        ts, line = since[0]
        assert line == "late"
        assert ts >= t2

    def test_search(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("INFO: Server started")
        buf.append("DEBUG: Loading config")
        buf.append("ERROR: Something failed")
        buf.append("INFO: Connected")
        
        errors = buf.search("error", n=10)
        assert len(errors) == 1
        assert "ERROR" in errors[0]
        
        infos = buf.search("info", n=10)
        assert len(infos) == 2

    def test_search_case_insensitive(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("ERROR: Failed")
        buf.append("error: also failed")
        buf.append("Error: mixed case")
        
        results = buf.search("error", n=10)
        assert len(results) == 3

    def test_search_limit(self):
        buf = RollingLogBuffer(max_lines=100)
        for i in range(10):
            buf.append(f"test line {i}")
        
        results = buf.search("test", n=3)
        assert len(results) == 3
        # Should return most recent matches
        assert "line 7" in results[0]
        assert "line 8" in results[1]
        assert "line 9" in results[2]

    def test_search_zero_limit(self):
        buf = RollingLogBuffer(max_lines=100)
        buf.append("test")
        assert buf.search("test", n=0) == []
        assert buf.search("test", n=-1) == []

    def test_thread_safety(self):
        """Test concurrent access to buffer."""
        buf = RollingLogBuffer(max_lines=1000)
        errors = []
        
        def writer():
            try:
                for i in range(100):
                    buf.append(f"line {i}")
            except Exception as e:
                errors.append(e)
        
        def reader():
            try:
                for _ in range(100):
                    buf.get_recent(50)
                    buf.search("line", n=10)
            except Exception as e:
                errors.append(e)
        
        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0


class TestStdoutReader:
    """Tests for StdoutReader class."""

    def test_reads_lines_into_buffer(self):
        buf = RollingLogBuffer(max_lines=100)
        stream = io.StringIO("line1\nline2\nline3\n")
        
        reader = StdoutReader(stream, buf)
        reader.start()
        time.sleep(0.1)  # Give it time to read
        reader.stop()
        
        lines = buf.get_recent(10)
        assert "line1" in lines
        assert "line2" in lines
        assert "line3" in lines

    def test_on_line_callback(self):
        buf = RollingLogBuffer(max_lines=100)
        stream = io.StringIO("test line\n")
        
        callback_lines = []
        def on_line(line):
            callback_lines.append(line)
        
        reader = StdoutReader(stream, buf, on_line=on_line)
        reader.start()
        time.sleep(0.1)
        reader.stop()
        
        assert len(callback_lines) == 1
        assert "test line" in callback_lines[0]

    def test_callback_exception_doesnt_break_reader(self):
        buf = RollingLogBuffer(max_lines=100)
        stream = io.StringIO("line1\nline2\n")
        
        def bad_callback(line):
            raise ValueError("intentional error")
        
        reader = StdoutReader(stream, buf, on_line=bad_callback)
        reader.start()
        time.sleep(0.1)
        reader.stop()
        
        # Should still have captured lines despite callback errors
        lines = buf.get_recent(10)
        assert "line1" in lines
        assert "line2" in lines

    def test_stop_is_idempotent(self):
        buf = RollingLogBuffer(max_lines=100)
        stream = io.StringIO("")
        
        reader = StdoutReader(stream, buf)
        reader.start()
        reader.stop()
        reader.stop()  # Should not error
