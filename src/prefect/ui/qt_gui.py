from __future__ import annotations

import sys
import time
import logging

from PySide6 import QtCore, QtWidgets

from prefect.config import get_settings
from prefect.mcp.tools import PrefectCore
from prefect.persona import Persona, PersonaParameters, PARAMETER_DESCRIPTIONS

# Configure logging to print to stderr so we can see it in the terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def _fmt_time(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        logging.info("Initializing MainWindow...")
        self.settings = get_settings()
        
        # Load persistent preferences
        self.prefs = QtCore.QSettings("Prefect", "NecesseSteward")
        saved_url = self.prefs.value("ollama_url")
        saved_model = self.prefs.value("ollama_model")
        
        if saved_url:
            self.settings.ollama_url = str(saved_url)
        if saved_model:
            self.settings.model = str(saved_model)

        self.core = PrefectCore(self.settings)
        # Ensure core is synced with loaded prefs
        self.core.set_ollama(base_url=self.settings.ollama_url, model=self.settings.model)
        
        # Start agent services (chat thread) immediately for offline chat support
        self.core.start_agent()
        
        logging.info("PrefectCore initialized.")

        self.setWindowTitle("Prefect")
        self.resize(1100, 700)

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        outer = QtWidgets.QHBoxLayout(root)

        # Left nav
        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(170)
        self.nav.addItem("Necesse")
        self.nav.addItem("Chat")
        self.nav.addItem("Admin")
        self.nav.setCurrentRow(0)
        outer.addWidget(self.nav)

        # Pages
        self.pages = QtWidgets.QStackedWidget()
        outer.addWidget(self.pages, 1)

        self.server_page = self._build_server_page()
        self.chat_page = self._build_chat_page()
        self.admin_page = self._build_admin_page()
        self.pages.addWidget(self.server_page)
        self.pages.addWidget(self.chat_page)
        self.pages.addWidget(self.admin_page)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        # Poll timer
        self._last_chat_ts = 0.0
        self._last_activity_ts = 0.0
        self._last_log_ts = 0.0
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

        # Command Input
        cmd_group = QtWidgets.QGroupBox("Console Command")
        cmd_layout = QtWidgets.QHBoxLayout(cmd_group)
        self.cmd_input = QtWidgets.QLineEdit()
        self.cmd_input.setPlaceholderText("Enter server command...")
        self.cmd_run = QtWidgets.QPushButton("Run")
        cmd_layout.addWidget(self.cmd_input, 1)
        cmd_layout.addWidget(self.cmd_run)
        v.addWidget(cmd_group)

        mid = QtWidgets.QHBoxLayout()

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Players"))
        self.players_list = QtWidgets.QListWidget()
        left.addWidget(self.players_list, 1)
        mid.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("Console Output"))
        self.console_feed = QtWidgets.QPlainTextEdit()
        self.console_feed.setReadOnly(True)
        self.console_feed.setMaximumBlockCount(2000)
        right.addWidget(self.console_feed, 1)
        mid.addLayout(right, 2)

        v.addLayout(mid, 1)

        # Troubleshooting
        trouble_group = QtWidgets.QGroupBox("Troubleshooting")
        t_layout = QtWidgets.QHBoxLayout(trouble_group)
        self.kill_zombies_btn = QtWidgets.QPushButton("Kill Zombie Server Processes")
        self.kill_zombies_btn.setToolTip("Force kill any lingering 'Server.jar' or 'StartServer' processes.")
        self.kill_zombies_btn.setStyleSheet("background-color: #ffcccc; color: #330000;")
        t_layout.addWidget(self.kill_zombies_btn)
        
        self.discover_btn = QtWidgets.QPushButton("Discover Commands")
        self.discover_btn.setToolTip("Run paginated 'help' to discover all server commands and generate allowlist.")
        self.discover_btn.setStyleSheet("background-color: #ccffcc; color: #003300;")
        t_layout.addWidget(self.discover_btn)
        
        t_layout.addStretch(1)
        v.addWidget(trouble_group)

        self.start_button.clicked.connect(self._on_start)
        self.stop_button.clicked.connect(self._on_stop)
        self.cmd_run.clicked.connect(self._run_console_command)
        self.cmd_input.returnPressed.connect(self._run_console_command)
        self.kill_zombies_btn.clicked.connect(self._on_kill_zombies)
        self.discover_btn.clicked.connect(self._on_discover_commands)
        return w

    def _build_chat_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)

        # Toggles
        toggles = QtWidgets.QHBoxLayout()
        self.chk_necesse = QtWidgets.QCheckBox("Necesse Chat")
        self.chk_necesse.setChecked(True)
        self.chk_system = QtWidgets.QCheckBox("System Activity")
        self.chk_system.setChecked(True)
        self.chk_admin = QtWidgets.QCheckBox("Admin/AI Chat")
        self.chk_admin.setChecked(True)
        
        toggles.addWidget(self.chk_necesse)
        toggles.addWidget(self.chk_system)
        toggles.addWidget(self.chk_admin)
        toggles.addStretch(1)
        v.addLayout(toggles)

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
        
        # Connect toggles to refresh
        self.chk_necesse.stateChanged.connect(self._refresh_chat_view)
        self.chk_system.stateChanged.connect(self._refresh_chat_view)
        self.chk_admin.stateChanged.connect(self._refresh_chat_view)
        
        return w

    def _build_admin_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        
        # Use a scroll area for the admin page
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(scroll_content)

        # --- Ollama Configuration ---
        oll = QtWidgets.QGroupBox("Ollama Configuration")
        oll_l = QtWidgets.QGridLayout(oll)
        self.ollama_url = QtWidgets.QLineEdit(self.settings.ollama_url)
        self.ollama_model = QtWidgets.QComboBox()
        self.ollama_model.setEditable(True)
        
        # If we have a saved model, use it. Otherwise start blank.
        saved_model = self.prefs.value("ollama_model")
        if saved_model:
            self.ollama_model.addItem(str(saved_model))
        else:
            self.ollama_model.addItem("")
        
        self.ollama_refresh = QtWidgets.QPushButton("Refresh Models")
        self.ollama_apply = QtWidgets.QPushButton("Apply Settings")
        self.ollama_test = QtWidgets.QPushButton("Test Connection")
        self.admin_status = QtWidgets.QLabel("")
        self.admin_status.setStyleSheet("color: #666;")

        oll_l.addWidget(QtWidgets.QLabel("Server URL"), 0, 0)
        oll_l.addWidget(self.ollama_url, 0, 1, 1, 3)
        oll_l.addWidget(QtWidgets.QLabel("Model"), 1, 0)
        oll_l.addWidget(self.ollama_model, 1, 1, 1, 3)
        
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.ollama_refresh)
        btn_row.addWidget(self.ollama_test)
        btn_row.addWidget(self.ollama_apply)
        oll_l.addLayout(btn_row, 2, 0, 1, 4)
        oll_l.addWidget(self.admin_status, 3, 0, 1, 4)
        
        v.addWidget(oll)

        # --- Persona Selection ---
        persona_sel = QtWidgets.QGroupBox("Persona Selection")
        ps_l = QtWidgets.QHBoxLayout(persona_sel)
        self.persona_combo = QtWidgets.QComboBox()
        self._refresh_persona_list()
        self.persona_load_btn = QtWidgets.QPushButton("Load")
        self.persona_delete_btn = QtWidgets.QPushButton("Delete")
        ps_l.addWidget(QtWidgets.QLabel("Active Persona:"))
        ps_l.addWidget(self.persona_combo, 1)
        ps_l.addWidget(self.persona_load_btn)
        ps_l.addWidget(self.persona_delete_btn)
        v.addWidget(persona_sel)

        # --- Persona Editor ---
        persona_edit = QtWidgets.QGroupBox("Persona Editor")
        pe_l = QtWidgets.QVBoxLayout(persona_edit)
        
        # Name
        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Persona Name:"))
        self.persona_name = QtWidgets.QLineEdit()
        self.persona_name.setPlaceholderText("Enter a name for this persona...")
        name_row.addWidget(self.persona_name, 1)
        pe_l.addLayout(name_row)
        
        # System Prompt
        pe_l.addWidget(QtWidgets.QLabel("System Prompt (instructions for the AI):"))
        self.system_prompt_edit = QtWidgets.QPlainTextEdit()
        self.system_prompt_edit.setMaximumHeight(100)
        self.system_prompt_edit.setPlaceholderText("e.g., You are Prefect, a helpful steward AI...")
        pe_l.addWidget(self.system_prompt_edit)
        
        # Persona Prompt
        pe_l.addWidget(QtWidgets.QLabel("Personality Description (how the AI should behave):"))
        self.persona_prompt_edit = QtWidgets.QPlainTextEdit()
        self.persona_prompt_edit.setMaximumHeight(80)
        self.persona_prompt_edit.setPlaceholderText("e.g., Prefect is friendly, concise, and professional...")
        pe_l.addWidget(self.persona_prompt_edit)
        
        v.addWidget(persona_edit)

        # --- Parameter Tuning ---
        params_box = QtWidgets.QGroupBox("Response Parameters")
        params_l = QtWidgets.QGridLayout(params_box)
        
        self.param_sliders: dict[str, QtWidgets.QSlider] = {}
        self.param_labels: dict[str, QtWidgets.QLabel] = {}
        
        row = 0
        for param_name, info in PARAMETER_DESCRIPTIONS.items():
            # Label with description
            label = QtWidgets.QLabel(f"<b>{info['label']}</b>")
            label.setToolTip(info['description'])
            params_l.addWidget(label, row, 0, 1, 2)
            
            # Low/High hints
            hint_low = QtWidgets.QLabel(f"← {info['low']}")
            hint_low.setStyleSheet("color: #666; font-size: 10px;")
            hint_high = QtWidgets.QLabel(f"{info['high']} →")
            hint_high.setStyleSheet("color: #666; font-size: 10px;")
            hint_high.setAlignment(QtCore.Qt.AlignRight)
            
            params_l.addWidget(hint_low, row + 1, 0)
            params_l.addWidget(hint_high, row + 1, 1)
            
            # Slider
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            min_val = info['min']
            max_val = info['max']
            step = info['step']
            
            # Convert to int for slider (multiply by scale factor)
            if isinstance(step, float):
                scale = int(1 / step)
            else:
                scale = 1
            
            slider.setMinimum(int(min_val * scale))
            slider.setMaximum(int(max_val * scale))
            slider.setValue(int(info['default'] * scale))
            slider.setProperty("scale", scale)
            slider.setProperty("param_name", param_name)
            
            # Value label
            value_label = QtWidgets.QLabel(str(info['default']))
            value_label.setMinimumWidth(40)
            value_label.setAlignment(QtCore.Qt.AlignCenter)
            
            slider.valueChanged.connect(lambda val, lbl=value_label, s=scale: lbl.setText(f"{val/s:.2f}" if s > 1 else str(val)))
            
            params_l.addWidget(slider, row + 2, 0)
            params_l.addWidget(value_label, row + 2, 1)
            
            self.param_sliders[param_name] = slider
            self.param_labels[param_name] = value_label
            
            row += 3
        
        v.addWidget(params_box)

        # --- Save Persona ---
        save_box = QtWidgets.QGroupBox("Save Persona")
        save_l = QtWidgets.QHBoxLayout(save_box)
        self.persona_save_btn = QtWidgets.QPushButton("Save Current as New Persona")
        self.persona_save_btn.setStyleSheet("background-color: #ccffcc;")
        self.persona_update_btn = QtWidgets.QPushButton("Update Selected Persona")
        save_l.addWidget(self.persona_save_btn)
        save_l.addWidget(self.persona_update_btn)
        v.addWidget(save_box)
        
        v.addStretch(1)
        
        scroll.setWidget(scroll_content)
        
        # Main layout for the page
        main_layout = QtWidgets.QVBoxLayout(w)
        main_layout.addWidget(scroll)

        # Connect signals
        self.ollama_refresh.clicked.connect(self._refresh_models)
        self.ollama_apply.clicked.connect(self._apply_ollama)
        self.ollama_test.clicked.connect(self._test_ollama)
        self.persona_load_btn.clicked.connect(self._load_persona)
        self.persona_delete_btn.clicked.connect(self._delete_persona)
        self.persona_save_btn.clicked.connect(self._save_persona_new)
        self.persona_update_btn.clicked.connect(self._update_persona)
        
        # Load the active persona into the editor
        self._load_active_persona_to_editor()
        
        return w

    def _refresh_persona_list(self) -> None:
        self.persona_combo.clear()
        personas = self.core.persona_manager.list_personas()
        active = self.core.persona_manager.get_active_name()
        for name in personas:
            self.persona_combo.addItem(name)
        idx = self.persona_combo.findText(active)
        if idx >= 0:
            self.persona_combo.setCurrentIndex(idx)

    def _load_active_persona_to_editor(self) -> None:
        persona = self.core.persona_manager.get_active_persona()
        self.persona_name.setText(persona.name)
        self.system_prompt_edit.setPlainText(persona.system_prompt)
        self.persona_prompt_edit.setPlainText(persona.persona_prompt)
        
        # Load parameters
        params = persona.parameters
        for param_name, slider in self.param_sliders.items():
            scale = slider.property("scale")
            value = getattr(params, param_name, PARAMETER_DESCRIPTIONS[param_name]['default'])
            slider.setValue(int(value * scale))

    def _get_editor_persona(self) -> Persona:
        """Build a Persona object from the current editor values."""
        params = PersonaParameters(
            temperature=self.param_sliders["temperature"].value() / self.param_sliders["temperature"].property("scale"),
            top_p=self.param_sliders["top_p"].value() / self.param_sliders["top_p"].property("scale"),
            top_k=self.param_sliders["top_k"].value(),
            repeat_penalty=self.param_sliders["repeat_penalty"].value() / self.param_sliders["repeat_penalty"].property("scale"),
        )
        return Persona(
            name=self.persona_name.text().strip() or "Unnamed",
            system_prompt=self.system_prompt_edit.toPlainText().strip(),
            persona_prompt=self.persona_prompt_edit.toPlainText().strip(),
            parameters=params,
        )

    def _load_persona(self) -> None:
        name = self.persona_combo.currentText()
        if self.core.persona_manager.set_active(name):
            self._load_active_persona_to_editor()
            self._append_activity(f"Loaded persona: {name}")
        else:
            self._append_activity(f"Failed to load persona: {name}")

    def _delete_persona(self) -> None:
        name = self.persona_combo.currentText()
        if name == "Default":
            QtWidgets.QMessageBox.warning(self, "Cannot Delete", "The Default persona cannot be deleted.")
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Confirm Delete", f"Delete persona '{name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            if self.core.persona_manager.delete_persona(name):
                self._refresh_persona_list()
                self._load_active_persona_to_editor()
                self._append_activity(f"Deleted persona: {name}")

    def _save_persona_new(self) -> None:
        persona = self._get_editor_persona()
        if not persona.name or persona.name == "Unnamed":
            QtWidgets.QMessageBox.warning(self, "Name Required", "Please enter a name for the persona.")
            return
        if persona.name in self.core.persona_manager.list_personas():
            reply = QtWidgets.QMessageBox.question(
                self, "Overwrite?", f"A persona named '{persona.name}' already exists. Overwrite?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
        self.core.persona_manager.save_persona(persona)
        self.core.persona_manager.set_active(persona.name)
        self._refresh_persona_list()
        self._append_activity(f"Saved and activated persona: {persona.name}")

    def _update_persona(self) -> None:
        selected_name = self.persona_combo.currentText()
        persona = self._get_editor_persona()
        # Use the selected name, not the editor name (to update in place)
        persona = Persona(
            name=selected_name,
            system_prompt=persona.system_prompt,
            persona_prompt=persona.persona_prompt,
            parameters=persona.parameters,
        )
        self.core.persona_manager.save_persona(persona)
        self._append_activity(f"Updated persona: {selected_name}")

    def _on_start(self) -> None:
        logging.info("Start button clicked")
        resp = self.core.start_server()
        logging.info(f"start_server response: {resp}")
        if resp.get("ok"):
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
        else:
            self._append_activity(f"start failed: {resp}")

    def _on_stop(self) -> None:
        logging.info("Stop button clicked")
        resp = self.core.stop_server()
        logging.info(f"stop_server response: {resp}")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if not resp.get("ok"):
            self._append_activity(f"stop failed: {resp}")

    def _on_kill_zombies(self) -> None:
        reply = QtWidgets.QMessageBox.question(
            self, 
            "Confirm Kill", 
            "This will force-kill any process matching 'Server.jar' or 'StartServer'.\n\nUse this if the server fails to start with 'Address already in use'.\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            resp = self.core.kill_zombie_processes()
            self._append_activity(f"Kill zombies: {resp}")
            QtWidgets.QMessageBox.information(self, "Result", f"Result: {resp}")

    def _on_discover_commands(self) -> None:
        """Run command discovery via paginated help."""
        # Check if server is running
        status = self.core.get_status()
        if not status.get("running"):
            QtWidgets.QMessageBox.warning(
                self,
                "Server Not Running",
                "The server must be running to discover commands.\n\nStart the server first, complete any startup prompts, then try again."
            )
            return
        
        reply = QtWidgets.QMessageBox.question(
            self,
            "Discover Commands",
            "This will run 'help' and paginate through all available commands.\n\n"
            "Results will be saved to:\n"
            f"  {self.settings.server_root}/prefect/\n\n"
            "This may take a few seconds. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        
        self._append_activity("Starting command discovery...")
        self.discover_btn.setEnabled(False)
        self.discover_btn.setText("Discovering...")
        
        # Run discovery (this is synchronous but reasonably fast)
        QtWidgets.QApplication.processEvents()
        try:
            resp = self.core.bootstrap_allowlist()
            
            if resp.get("ok"):
                msg = (
                    f"Discovery complete!\n\n"
                    f"Commands discovered: {resp.get('commands_discovered', 0)}\n"
                    f"Allowed: {resp.get('allowed_count', 0)}\n"
                    f"Denied: {resp.get('denied_count', 0)}\n"
                    f"Reason: {resp.get('termination_reason', 'unknown')}\n\n"
                    f"Files saved to:\n{resp.get('snapshot_path', 'N/A')}"
                )
                self._append_activity(f"Discovery: {resp.get('commands_discovered', 0)} commands found")
                QtWidgets.QMessageBox.information(self, "Discovery Complete", msg)
            else:
                self._append_activity(f"Discovery failed: {resp.get('error')}")
                QtWidgets.QMessageBox.warning(self, "Discovery Failed", f"Error: {resp.get('error')}")
        except Exception as e:
            self._append_activity(f"Discovery error: {e}")
            QtWidgets.QMessageBox.warning(self, "Discovery Error", f"Exception: {e}")
        finally:
            self.discover_btn.setEnabled(True)
            self.discover_btn.setText("Discover Commands")

    def _run_console_command(self) -> None:
        cmd = self.cmd_input.text().strip()
        logging.info(f"Running command: {cmd}")
        if not cmd:
            return
        self.cmd_input.clear()
        self._append_activity(f"> {cmd}")
        resp = self.core.run_command(cmd)
        logging.info(f"run_command response: {resp}")
        if resp.get("ok"):
            # Don't print output here, as it will appear in the log stream via _tick
            # if resp.get("output"):
            #     self._append_activity(f"Output: {resp.get('output')}")
            pass
        else:
            self._append_activity(f"Error: {resp.get('error')}")

    def _apply_ollama(self) -> None:
        url = self.ollama_url.text().strip()
        model = self.ollama_model.currentText().strip()
        if not url:
            self.admin_status.setText("Error: URL is empty")
            return
        if not model:
            self.admin_status.setText("Error: Model is empty")
            return
        self.core.set_ollama(base_url=url, model=model)
        
        # Save to persistent prefs
        self.prefs.setValue("ollama_url", url)
        self.prefs.setValue("ollama_model", model)
        
        self.admin_status.setText(f"Settings applied: {url} | {model}")
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

    def _test_ollama(self) -> None:
        self.admin_status.setText("Testing Ollama connection...")
        self._append_activity("Testing Ollama connection...")
        # Force a repaint so user sees "Testing..." immediately
        QtWidgets.QApplication.processEvents()
        
        resp = self.core.list_ollama_models()
        if resp.get("ok"):
            msg = "Ollama connection successful!"
            self.admin_status.setText(msg)
            self._append_activity(msg)
        else:
            msg = f"Ollama connection failed: {resp.get('error')}"
            self.admin_status.setText(msg)
            self._append_activity(msg)

    def _send_chat(self) -> None:
        text = self.chat_input.text().strip()
        logging.info(f"Sending chat: {text}")
        if not text:
            return
        self.chat_input.clear()

        # Use send_chat_message which handles both online and offline scenarios
        resp = self.core.send_chat_message(text)
        logging.info(f"send_chat_message response: {resp}")
        if not resp.get("ok"):
            self._append_activity(f"send failed: {resp}")

    def _refresh_chat_view(self) -> None:
        # Clear and rebuild chat from scratch (expensive but simple for now)
        # Ideally we'd just filter the stream, but we only store the last N events in memory in the GUI?
        # Actually, we append to QPlainTextEdit. To filter, we need to clear and re-add.
        # But we don't keep the full history in the GUI class, only what's in the widget.
        # We might need to fetch history from core again or just accept that toggles apply to NEW messages?
        # User asked to "toggle necesse and bring in just the chat stream".
        # This implies filtering the view.
        # Let's clear and re-fetch recent history from core.
        self.chat_transcript.clear()
        self._last_chat_ts = 0.0
        self._last_activity_ts = 0.0
        # _tick will re-populate
        
    def _append_activity(self, line: str) -> None:
        ts = time.time()
        self.console_feed.appendPlainText(f"[{_fmt_time(ts)}] {line}")

    def _tick(self) -> None:
        try:
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

            # Console Output (Server Logs)
            logs = self.core.get_logs_since(self._last_log_ts)
            if logs:
                for ts, line in logs:
                    self.console_feed.appendPlainText(line.rstrip())
                # Update last_log_ts to the max timestamp seen + epsilon
                self._last_log_ts = max(ts for ts, _ in logs) + 0.000001

            # Chat Transcript (Chat + Activity)
            chat = self.core.get_chat_events(since_ts=self._last_chat_ts)
            activity = self.core.get_activity_events(since_ts=self._last_activity_ts)
            
            # Merge and sort
            events = []
            if chat:
                for ts, who, msg in chat:
                    # Filter based on toggles
                    is_admin = who in ("Admin", "Prefect")
                    if is_admin and not self.chk_admin.isChecked():
                        continue
                    if not is_admin and not self.chk_necesse.isChecked():
                        continue
                    events.append((ts, f"{who}: {msg}"))
                self._last_chat_ts = max(ts for ts, _, _ in chat) + 0.000001
            
            if activity:
                if self.chk_system.isChecked():
                    for ts, msg in activity:
                        events.append((ts, f"[System] {msg}"))
                self._last_activity_ts = max(ts for ts, _ in activity) + 0.000001
            
            events.sort(key=lambda x: x[0])
            
            for ts, msg in events:
                self.chat_transcript.appendPlainText(f"[{_fmt_time(ts)}] {msg}")

        except Exception as e:
            logging.error(f"Error in _tick: {e}")



def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
