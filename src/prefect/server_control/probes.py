from __future__ import annotations

import socket


def tcp_port_open(host: str, port: int, *, timeout_seconds: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return True
    except OSError:
        return False
