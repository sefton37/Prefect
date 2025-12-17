from __future__ import annotations

import sys
import time

from prefect.config import get_settings
from prefect.mcp.tools import PrefectCore


def main() -> None:
    # Import PySide6 only when launching GUI.
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    settings = get_settings()
    core = PrefectCore(settings)
    core.start()

    app = QApplication(sys.argv)

    root = QWidget()
    root.setWindowTitle("Prefect (Necesse)")

    status_label = QLabel("status: starting...")
    logs = QTextEdit()
    logs.setReadOnly(True)

    cmd = QLineEdit()
    cmd.setPlaceholderText("Allowed command (e.g. help)")
    send_cmd = QPushButton("Send")

    reply = QLineEdit()
    reply.setPlaceholderText("Startup reply (number or y/n)")
    send_reply = QPushButton("Reply")

    summarize = QPushButton("Summarize last 50")

    def refresh() -> None:
        st = core.get_status()
        status_label.setText(
            f"running={st.get('running')} pid={st.get('pid')} port_open={st.get('game_port_open')}"
        )
        recent = core.get_recent_logs(200)
        logs.setPlainText("\n".join(recent))
        logs.verticalScrollBar().setValue(logs.verticalScrollBar().maximum())

    def on_send_cmd() -> None:
        text = cmd.text().strip()
        if not text:
            return
        resp = core.run_command(text)
        core.log_buffer.append(f"[Prefect] run_command ok={resp.get('ok')} err={resp.get('error')}")
        cmd.clear()
        time.sleep(0.05)
        refresh()

    def on_send_reply() -> None:
        text = reply.text().strip()
        if not text:
            return
        resp = core.startup_reply(text)
        core.log_buffer.append(f"[Prefect] startup_reply ok={resp.get('ok')} err={resp.get('error')}")
        reply.clear()
        time.sleep(0.05)
        refresh()

    def on_summarize() -> None:
        # Run async ollama call in the simplest way: block briefly.
        import asyncio

        resp = asyncio.run(core.summarize_recent_logs(50))
        if resp.get("ok"):
            core.log_buffer.append("[Prefect] summary: " + resp.get("summary", ""))
        else:
            core.log_buffer.append("[Prefect] summarize_failed: " + resp.get("error", ""))
        refresh()

    send_cmd.clicked.connect(on_send_cmd)
    cmd.returnPressed.connect(on_send_cmd)

    send_reply.clicked.connect(on_send_reply)
    reply.returnPressed.connect(on_send_reply)

    summarize.clicked.connect(on_summarize)

    layout = QVBoxLayout()
    layout.addWidget(status_label)
    layout.addWidget(logs)

    row1 = QHBoxLayout()
    row1.addWidget(cmd)
    row1.addWidget(send_cmd)
    layout.addLayout(row1)

    row2 = QHBoxLayout()
    row2.addWidget(reply)
    row2.addWidget(send_reply)
    layout.addLayout(row2)

    layout.addWidget(summarize)

    root.setLayout(layout)

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(500)

    refresh()
    root.resize(900, 700)
    root.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
