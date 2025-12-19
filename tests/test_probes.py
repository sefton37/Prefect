"""Tests for prefect.server_control.probes module."""
from __future__ import annotations

import socket
import threading

import pytest

from prefect.server_control.probes import tcp_port_open


class TestTcpPortOpen:
    """Tests for tcp_port_open function."""

    def test_returns_false_for_closed_port(self):
        # Port 59999 is very unlikely to be in use
        assert tcp_port_open("127.0.0.1", 59999, timeout_seconds=0.1) is False

    def test_returns_false_for_invalid_host(self):
        assert tcp_port_open("192.0.2.1", 80, timeout_seconds=0.1) is False  # TEST-NET-1

    def test_returns_true_for_open_port(self):
        # Create a temporary server socket
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen(1)
        
        try:
            assert tcp_port_open("127.0.0.1", port, timeout_seconds=1.0) is True
        finally:
            server.close()

    def test_timeout_works(self):
        # Test that timeout is respected
        import time
        start = time.time()
        tcp_port_open("192.0.2.1", 80, timeout_seconds=0.5)
        elapsed = time.time() - start
        # Should complete within timeout (with some slack)
        assert elapsed < 2.0
