from __future__ import annotations

import json
import re
from pathlib import Path


class CommandCatalogError(RuntimeError):
    pass


_VALID_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def load_command_names(path: Path | None) -> list[str]:
    """Load a list of server console command names from JSON.

    Format:
      {"commands": ["help", "status", ...]}

    If the file is missing or path is None, returns a conservative default.
    """

    default = ["help", "?", "status", "players", "list", "say"]

    if path is None:
        return default

    if not path.exists():
        return default

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CommandCatalogError(f"Failed to parse commands file: {path} ({exc})")

    commands = data.get("commands")
    if not isinstance(commands, list):
        raise CommandCatalogError(f"Invalid commands file format (expected commands list): {path}")

    names: list[str] = []
    seen: set[str] = set()
    for item in commands:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name:
            continue
        if not _VALID_NAME.match(name):
            raise CommandCatalogError(f"Invalid command name in catalog: {name!r}")
        if name not in seen:
            seen.add(name)
            names.append(name)

    return names or default
