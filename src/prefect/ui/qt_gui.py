from __future__ import annotations

import sys
import time

from PySide6 import QtCore, QtWidgets

from prefect.config import get_settings
from prefect.mcp.tools import PrefectCore


def _fmt_time(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()
        self.core = PrefectCore(self.settings)

        self.setWindowTitle("Prefect")
        self.resize(1100, 700)

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        outer = QtWidgets.QHBoxLayout(root)

        # Left nav
        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(170)
        self.nav.addItem("Server")
        self.nav.addItem("Chat")
        self.nav.setCurrentRow(0)
        outer.addWidget(self.nav)

        # Pages
        self.pages = QtWidgets.QStackedWidget()
        outer.addWidget(self.pages, 1)

        self.server_page = self._build_server_page()
        self.chat_page = self._build_chat_page()
        self.pages.addWidget(self.server_page)
        self.pages.addWidget(self.chat_page)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        # Poll timer
        self._last_chat_ts = 0.0
        self._last_activity_ts = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_server_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)

        top = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Status: idle")
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        top.addWidget(self.status_label, 1)

        self.start_button = QtWidgets.QPushButton("Start")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        top.addWidget(self.start_button)
        top.addWidget(self.stop_button)
        v.addLayout(top)

        oll = QtWidgets.QGroupBox("Ollama")
        oll_l = QtWidgets.QGridLayout(oll)
        self.ollama_url = QtWidgets.QLineEdit(self.settings.ollama_url)
        self.ollama_model = QtWidgets.QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.addItem(self.settings.model)
        self.ollama_refresh = QtWidgets.QPushButton("Refresh")
        self.ollama_apply = QtWidgets.QPushButton("Apply")

        oll_l.addWidget(QtWidgets.QLabel("Server URL"), 0, 0)
        oll_l.addWidget(self.ollama_url, 0, 1, 1, 3)
        oll_l.addWidget(QtWidgets.QLabel("Model"), 1, 0)
        oll_l.addWidget(self.ollama_model, 1, 1)
        oll_l.addWidget(self.ollama_refresh, 1, 2)
        oll_l.addWidget(self.ollama_apply, 1, 3)
        v.addWidget(oll)

        mid = QtWidgets.QHBoxLayout()

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Players"))
        self.players_list = QtWidgets.QListWidget()
        left.addWidget(self.players_list, 1)
        mid.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("Activity"))
        self.activity_feed = QtWidgets.QPlainTextEdit()
        self.activity_feed.setReadOnly(True)
        self.activity_feed.setMaximumBlockCount(1000)
        right.addWidget(self.activity_feed, 1)
        mid.addLayout(right, 2)

        v.addLayout(mid, 1)

        self.start_button.clicked.connect(self._on_start)
        self.stop_button.clicked.connect(self._on_stop)
        self.ollama_refresh.clicked.connect(self._refresh_models)
        self.ollama_apply.clicked.connect(self._apply_ollama)
        return w

    def _build_chat_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)

        self.chat_transcript = QtWidgets.QPlainTextEdit()
        self.chat_transcript.setReadOnly(True)
        self.chat_transcript.setMaximumBlockCount(3000)
        v.addWidget(self.chat_transcript, 1)

        row = QtWidgets.QHBoxLayout()
        self.chat_input = QtWidgets.QLineEdit()
        self.chat_input.setPlaceholderText("Send a message (try starting with 'Prefect ...')")
        self.chat_send = QtWidgets.QPushButton("Send")
        row.addWidget(self.chat_input, 1)
        row.addWidget(self.chat_send)
        v.addLayout(row)

        self.chat_send.clicked.connect(self._send_chat)
        self.chat_input.returnPressed.connect(self._send_chat)
        return w

    def _on_start(self) -> None:
        resp = self.core.start_server()
        if resp.get("ok"):
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
        else:
            self._append_activity(f"start failed: {resp}")

    def _on_stop(self) -> None:
        resp = self.core.stop_server()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if not resp.get("ok"):
            self._append_activity(f"stop failed: {resp}")

    def _apply_ollama(self) -> None:
        url = self.ollama_url.text().strip()
        model = self.ollama_model.currentText().strip()
        if not url:
            self._append_activity("ollama url is empty")
            return
        if not model:
            self._append_activity("ollama model is empty")
            return
        self.core.set_ollama(base_url=url, model=model)
        self._append_activity(f"ollama set: {url} | model={model}")

    def _refresh_models(self) -> None:
        url = self.ollama_url.text().strip()
        if not url:
            self._append_activity("ollama url is empty")
            return

        # Use the typed URL for discovery.
        self.core.set_ollama(base_url=url, model=self.ollama_model.currentText().strip() or self.settings.model)
        resp = self.core.list_ollama_models()
        if not resp.get("ok"):
            self._append_activity(f"model refresh failed: {resp.get('error')}")
            return

        models = resp.get("models") or []
        current = self.ollama_model.currentText().strip() or self.settings.model
        self.ollama_model.clear()
        if current:
            self.ollama_model.addItem(current)
        for m in models:
            if m != current:
                self.ollama_model.addItem(m)
        self._append_activity(f"models loaded: {len(models)}")

    def _send_chat(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()

        # Broadcast to players via configured announce templates.
        resp = self.core.announce(text)
        if not resp.get("ok"):
            self._append_activity(f"send failed: {resp}")

    def _append_activity(self, line: str) -> None:
        ts = time.time()
        self.activity_feed.appendPlainText(f"[{_fmt_time(ts)}] {line}")

    def _tick(self) -> None:
        st = self.core.get_status()
        players = st.get("players_online", []) or []
        self.status_label.setText(
            f"Status: {st.get('state')} | players={len(players)}"
            + (f" | last_error={st.get('last_error')}" if st.get("last_error") else "")
        )

        # Update players list
        self.players_list.clear()
        for p in sorted(players):
            self.players_list.addItem(p)

        # Less-verbose activity feed (join/leave + manual notes)
        activity = self.core.get_activity_events(since_ts=self._last_activity_ts)
        if activity:
            self._last_activity_ts = max(ts for ts, _ in activity)
            for ts, msg in activity:
                self.activity_feed.appendPlainText(f"[{_fmt_time(ts)}] {msg}")

        # Chatroom transcript (player messages + server messages + Prefect replies when they appear)
        chat = self.core.get_chat_events(since_ts=self._last_chat_ts)
        if chat:
            self._last_chat_ts = max(ts for ts, _, _ in chat)
            for ts, who, msg in chat:
                self.chat_transcript.appendPlainText(f"[{_fmt_time(ts)}] {who}: {msg}")


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
