"""Pytest configuration and shared fixtures."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory that's cleaned up after the test."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def commands_json(temp_dir: Path) -> Path:
    """Create a temporary commands.json file."""
    path = temp_dir / "commands.json"
    data = {"commands": ["help", "status", "say", "kick", "ban"]}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def personas_json(temp_dir: Path) -> Path:
    """Create a temporary personas.json file."""
    path = temp_dir / "personas.json"
    data = {
        "active": "TestPersona",
        "personas": {
            "Default": {
                "name": "Default",
                "system_prompt": "You are a test assistant.",
                "persona_prompt": "Be helpful.",
                "parameters": {
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "top_k": 40,
                    "repeat_penalty": 1.1,
                },
            },
            "TestPersona": {
                "name": "TestPersona",
                "system_prompt": "Custom system prompt.",
                "persona_prompt": "Custom persona.",
                "parameters": {
                    "temperature": 0.5,
                    "top_p": 0.8,
                    "top_k": 50,
                    "repeat_penalty": 1.2,
                },
            },
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
