from __future__ import annotations

from dataclasses import dataclass

from prefect.server_control.process_manager import NecesseProcessManager


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    output: str
    error: str | None = None


class CommandRunner:
    def __init__(self, manager: NecesseProcessManager):
        self._manager = manager

    def run(self, command: str) -> CommandResult:
        try:
            output = self._manager.run_command_capture(command)
            return CommandResult(ok=True, output=output)
        except Exception as exc:
            return CommandResult(ok=False, output="", error=str(exc))
