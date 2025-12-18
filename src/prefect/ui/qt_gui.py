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
    config_label = QLabel("config: ...")
    logs = QTextEdit()
    logs.setReadOnly(True)

    command_output = QTextEdit()
    command_output.setReadOnly(True)
    command_output.setPlaceholderText("Command output")

    cmd = QLineEdit()
    cmd.setPlaceholderText("Allowed command (e.g. help)")
    send_cmd = QPushButton("Send")

    send_help = QPushButton("Run help")
    send_announce = QPushButton("Announce test")

    reply = QLineEdit()
    reply.setPlaceholderText("Startup reply (number or y/n)")
    send_reply = QPushButton("Reply")

    summarize = QPushButton("Summarize last 50")

    llm_chat = QTextEdit()
    llm_chat.setReadOnly(True)
    llm_chat.setPlaceholderText("LLM chat transcript")
    llm_input = QLineEdit()
    llm_input.setPlaceholderText("Message Prefect (LLM test)")
    llm_send = QPushButton("Ask")

    def refresh() -> None:
        st = core.get_status()
        status_label.setText(
            f"running={st.get('running')} pid={st.get('pid')} port_open={st.get('game_port_open')}"
        )
        config_label.setText(
            "config: "
            f"ollama={settings.ollama_url} model={settings.model} "
            f"announce_templates={settings.announce_command_templates}"
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
        if resp.get("output"):
            command_output.setPlainText(resp.get("output", ""))
        cmd.clear()
        time.sleep(0.05)
        refresh()

    def on_run_help() -> None:
        resp = core.run_command("help")
        core.log_buffer.append(f"[Prefect] help ok={resp.get('ok')} err={resp.get('error')}")
        command_output.setPlainText(resp.get("output", ""))
        refresh()

    def on_announce_test() -> None:
        resp = core.announce("Prefect: announce test")
        core.log_buffer.append(
            f"[Prefect] announce_test ok={resp.get('ok')} sent={resp.get('sent')} err={resp.get('error')}"
        )
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

    def on_llm_send() -> None:
        text = llm_input.text().strip()
        if not text:
            return
        llm_input.clear()

        llm_chat.append(f"Player: {text}")

        import asyncio

        system_prompt = (
            "You are Prefect, a helpful steward AI for a Necesse dedicated server. "
            "Reply briefly and politely."
        )
        # Provide a small local transcript for context.
        transcript = llm_chat.toPlainText().splitlines()[-20:]
        user_prompt = "Conversation:\n" + "\n".join(transcript) + "\n\nReply as Prefect."
        try:
            reply_text = asyncio.run(core.ollama.generate(system_prompt, user_prompt))
            llm_chat.append(f"Prefect: {reply_text.strip()}")
        except Exception as exc:
            llm_chat.append(f"Prefect (error): {exc}")

    send_cmd.clicked.connect(on_send_cmd)
    cmd.returnPressed.connect(on_send_cmd)

    send_help.clicked.connect(on_run_help)
    send_announce.clicked.connect(on_announce_test)

    send_reply.clicked.connect(on_send_reply)
    reply.returnPressed.connect(on_send_reply)

    summarize.clicked.connect(on_summarize)

    llm_send.clicked.connect(on_llm_send)
    llm_input.returnPressed.connect(on_llm_send)

    layout = QVBoxLayout()
    layout.addWidget(status_label)
    layout.addWidget(config_label)
    layout.addWidget(logs)
    layout.addWidget(command_output)

    row1 = QHBoxLayout()
    row1.addWidget(cmd)
    row1.addWidget(send_cmd)
    row1.addWidget(send_help)
    row1.addWidget(send_announce)
    layout.addLayout(row1)

    row2 = QHBoxLayout()
    row2.addWidget(reply)
    row2.addWidget(send_reply)
    layout.addLayout(row2)

    layout.addWidget(summarize)

    layout.addWidget(llm_chat)
    row3 = QHBoxLayout()
    row3.addWidget(llm_input)
    row3.addWidget(llm_send)
    layout.addLayout(row3)

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
