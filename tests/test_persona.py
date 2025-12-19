"""Tests for prefect.persona module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prefect.persona import (
    Persona,
    PersonaParameters,
    PersonaManager,
    PARAMETER_DESCRIPTIONS,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_PERSONA_PROMPT,
)


class TestPersonaParameters:
    """Tests for PersonaParameters dataclass."""

    def test_default_values(self):
        params = PersonaParameters()
        assert params.temperature == 0.7
        assert params.top_p == 0.9
        assert params.top_k == 40
        assert params.repeat_penalty == 1.1

    def test_custom_values(self):
        params = PersonaParameters(
            temperature=0.5,
            top_p=0.8,
            top_k=50,
            repeat_penalty=1.2,
        )
        assert params.temperature == 0.5
        assert params.top_p == 0.8
        assert params.top_k == 50
        assert params.repeat_penalty == 1.2

    def test_to_dict(self):
        params = PersonaParameters(temperature=0.5)
        d = params.to_dict()
        assert d["temperature"] == 0.5
        assert "top_p" in d
        assert "top_k" in d
        assert "repeat_penalty" in d

    def test_from_dict(self):
        data = {"temperature": 0.3, "top_p": 0.7, "top_k": 30, "repeat_penalty": 1.0}
        params = PersonaParameters.from_dict(data)
        assert params.temperature == 0.3
        assert params.top_p == 0.7
        assert params.top_k == 30
        assert params.repeat_penalty == 1.0

    def test_from_dict_partial(self):
        """Missing keys should use defaults."""
        data = {"temperature": 0.3}
        params = PersonaParameters.from_dict(data)
        assert params.temperature == 0.3
        assert params.top_p == 0.9  # default
        assert params.top_k == 40  # default

    def test_from_dict_empty(self):
        params = PersonaParameters.from_dict({})
        assert params.temperature == 0.7  # default


class TestPersona:
    """Tests for Persona dataclass."""

    def test_default_values(self):
        persona = Persona(name="Test")
        assert persona.name == "Test"
        assert persona.system_prompt == DEFAULT_SYSTEM_PROMPT
        assert persona.persona_prompt == DEFAULT_PERSONA_PROMPT
        assert isinstance(persona.parameters, PersonaParameters)

    def test_custom_values(self):
        params = PersonaParameters(temperature=0.5)
        persona = Persona(
            name="Custom",
            system_prompt="Custom system",
            persona_prompt="Custom persona",
            parameters=params,
        )
        assert persona.name == "Custom"
        assert persona.system_prompt == "Custom system"
        assert persona.persona_prompt == "Custom persona"
        assert persona.parameters.temperature == 0.5

    def test_to_dict(self):
        persona = Persona(name="Test", system_prompt="sys", persona_prompt="per")
        d = persona.to_dict()
        assert d["name"] == "Test"
        assert d["system_prompt"] == "sys"
        assert d["persona_prompt"] == "per"
        assert "parameters" in d

    def test_from_dict(self):
        data = {
            "name": "Loaded",
            "system_prompt": "Loaded sys",
            "persona_prompt": "Loaded per",
            "parameters": {"temperature": 0.3},
        }
        persona = Persona.from_dict(data)
        assert persona.name == "Loaded"
        assert persona.system_prompt == "Loaded sys"
        assert persona.persona_prompt == "Loaded per"
        assert persona.parameters.temperature == 0.3

    def test_from_dict_minimal(self):
        """Missing fields should use defaults."""
        data = {"name": "Minimal"}
        persona = Persona.from_dict(data)
        assert persona.name == "Minimal"
        assert persona.system_prompt == DEFAULT_SYSTEM_PROMPT

    def test_get_full_system_prompt(self):
        persona = Persona(
            name="Test",
            system_prompt="You are helpful.",
            persona_prompt="Be concise.",
        )
        full = persona.get_full_system_prompt()
        assert "You are helpful." in full
        assert "Be concise." in full
        assert "Personality:" in full


class TestPersonaManager:
    """Tests for PersonaManager class."""

    def test_init_creates_default(self, temp_dir: Path):
        storage = temp_dir / "personas.json"
        manager = PersonaManager(storage_path=storage)
        assert "Default" in manager.list_personas()

    def test_load_existing_file(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        assert "Default" in manager.list_personas()
        assert "TestPersona" in manager.list_personas()
        assert manager.get_active_name() == "TestPersona"

    def test_get_active_persona(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        persona = manager.get_active_persona()
        assert persona.name == "TestPersona"
        assert persona.system_prompt == "Custom system prompt."

    def test_set_active(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        assert manager.set_active("Default")
        assert manager.get_active_name() == "Default"

    def test_set_active_nonexistent(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        assert not manager.set_active("NonExistent")
        assert manager.get_active_name() == "TestPersona"  # unchanged

    def test_save_persona(self, temp_dir: Path):
        storage = temp_dir / "personas.json"
        manager = PersonaManager(storage_path=storage)
        
        new_persona = Persona(name="NewPersona", system_prompt="New prompt")
        manager.save_persona(new_persona)
        
        assert "NewPersona" in manager.list_personas()
        
        # Verify persistence
        manager2 = PersonaManager(storage_path=storage)
        assert "NewPersona" in manager2.list_personas()
        loaded = manager2.get_persona("NewPersona")
        assert loaded is not None
        assert loaded.system_prompt == "New prompt"

    def test_delete_persona(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        assert manager.delete_persona("TestPersona")
        assert "TestPersona" not in manager.list_personas()
        # Should reset active to Default
        assert manager.get_active_name() == "Default"

    def test_delete_default_fails(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        assert not manager.delete_persona("Default")
        assert "Default" in manager.list_personas()

    def test_delete_nonexistent(self, personas_json: Path):
        manager = PersonaManager(storage_path=personas_json)
        assert not manager.delete_persona("NonExistent")

    def test_corrupted_file_recovery(self, temp_dir: Path):
        storage = temp_dir / "personas.json"
        storage.write_text("not valid json {{{", encoding="utf-8")
        
        # Should recover gracefully
        manager = PersonaManager(storage_path=storage)
        assert "Default" in manager.list_personas()

    def test_missing_active_fallback(self, temp_dir: Path):
        storage = temp_dir / "personas.json"
        data = {
            "active": "DeletedPersona",
            "personas": {
                "Default": {"name": "Default"},
            },
        }
        storage.write_text(json.dumps(data), encoding="utf-8")
        
        manager = PersonaManager(storage_path=storage)
        assert manager.get_active_name() == "Default"


class TestParameterDescriptions:
    """Tests for PARAMETER_DESCRIPTIONS constant."""

    def test_has_all_parameters(self):
        expected_params = ["temperature", "top_p", "top_k", "repeat_penalty"]
        for param in expected_params:
            assert param in PARAMETER_DESCRIPTIONS

    def test_description_structure(self):
        for name, info in PARAMETER_DESCRIPTIONS.items():
            assert "label" in info, f"{name} missing label"
            assert "description" in info, f"{name} missing description"
            assert "low" in info, f"{name} missing low"
            assert "high" in info, f"{name} missing high"
            assert "min" in info, f"{name} missing min"
            assert "max" in info, f"{name} missing max"
            assert "step" in info, f"{name} missing step"
            assert "default" in info, f"{name} missing default"

    def test_min_max_valid(self):
        for name, info in PARAMETER_DESCRIPTIONS.items():
            assert info["min"] <= info["default"] <= info["max"], f"{name} default out of range"
