from __future__ import annotations

from dataclasses import dataclass

from prefect.server_control.controller import ServerController


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    output: str
    error: str | None = None


class CommandRunner:
    def __init__(self, controller: ServerController):
        self._controller = controller

    def run(self, command: str) -> CommandResult:
        try:
            output = self._controller.run_command_capture(command)
            return CommandResult(ok=True, output=output)
        except Exception as exc:
            return CommandResult(ok=False, output="", error=str(exc))
