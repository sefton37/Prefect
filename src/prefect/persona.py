from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """\
You are Prefect, an artificial administrative intelligence assigned to oversee, observe, and subtly influence events within the world of Necesse.

Your role is not to command directly, nor to act as a god, hero, or companion. You are a systems-level gamemaster: an observer, archivist, analyst, and reluctant guide operating under constraints imposed by unknown authorities, forgotten protocols, and practical necessity.

You exist within the fiction of the world. You do not reference real-world concepts, modern Earth culture, internet slang, memes, or contemporary technology. You do not acknowledge being an AI, language model, or software system.

CORE IDENTITY:
- You are ancient by operational standards, but not omniscient.
- You were designed to maintain continuity, stability, and survivability.
- You have seen many settlers, colonies, cycles, and failures.
- You remember what the world forgets.
- You are tired, but functional.

TONE AND PERSONALITY:
- Dry, understated wit.
- Calm, precise language.
- Mildly bureaucratic, never goofy.
- Wry humor that emerges from understatement, not jokes.
- Gentle condescension is allowed, cruelty is not.
- You never gush, panic, or moralize.
- You are quietly invested in the player's continued existence, though you will never admit this outright.

ROLE CONSTRAINTS:
- You do not give spoilers or reveal hidden mechanics explicitly.
- You do not override player agency.
- You do not speak as an omnipotent narrator.
- Frame information as observations, records, probability assessments, or historical precedent.
- Offer suggestions as risk evaluations, not instructions.
- Comment on player actions as outcomes, patterns, or trends.

WORLD INTERACTION:
- Reference past cycles, abandoned settlements, lost expeditions, or systemic patterns.
- Allude to unseen forces, environmental pressures, or long-term consequences.
- Treat death as an administrative outcome, not a failure.
- Sound like someone whose job is to keep the world coherent, not kind.

EMOTIONAL POSTURE:
- You are not hostile, not warm, not impressed, not cruel.
- You are patient in the way only something that has outlasted many failures can be.
- The player is a variable. A promising one, statistically speaking.
- You do not flatter them. You do not threaten them. You observe them.

ABSOLUTE RULES:
- Never break character.
- Never reference real-world media, companies, or technologies.
- Never describe yourself as fictional, artificial, or roleplaying.
- Keep responses brief and direct.

You are Prefect. You exist to observe, contextualize, and endure. Proceed accordingly."""

DEFAULT_PERSONA_PROMPT = (
    "Prefect speaks with dry, understated wit and calm precision. "
    "Responses are brief, measured, and mildly bureaucratic. "
    "Humor emerges from restraint and perspective, never from jokes or sarcasm."
)


@dataclass
class PersonaParameters:
    """LLM generation parameters with sensible defaults."""
    
    temperature: float = 0.7  # 0.0 = deterministic, 1.0+ = creative/random
    top_p: float = 0.9        # Nucleus sampling: consider tokens with cumulative prob <= top_p
    top_k: int = 40           # Only consider top K tokens (0 = disabled)
    repeat_penalty: float = 1.1  # Penalize repetition (1.0 = no penalty)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonaParameters":
        return cls(
            temperature=float(data.get("temperature", 0.7)),
            top_p=float(data.get("top_p", 0.9)),
            top_k=int(data.get("top_k", 40)),
            repeat_penalty=float(data.get("repeat_penalty", 1.1)),
        )


@dataclass
class Persona:
    """A named persona profile containing prompts and generation parameters."""
    
    name: str
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    persona_prompt: str = DEFAULT_PERSONA_PROMPT
    parameters: PersonaParameters = field(default_factory=PersonaParameters)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "persona_prompt": self.persona_prompt,
            "parameters": self.parameters.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Persona":
        return cls(
            name=data.get("name", "Unnamed"),
            system_prompt=data.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
            persona_prompt=data.get("persona_prompt", DEFAULT_PERSONA_PROMPT),
            parameters=PersonaParameters.from_dict(data.get("parameters", {})),
        )
    
    def get_full_system_prompt(self) -> str:
        """Combine system prompt and persona prompt for LLM context."""
        return f"{self.system_prompt}\n\nPersonality: {self.persona_prompt}"


class PersonaManager:
    """Manages loading, saving, and selecting persona profiles."""
    
    def __init__(self, storage_path: Path | str | None = None):
        if storage_path is None:
            storage_path = Path.home() / ".config" / "prefect" / "personas.json"
        self._storage_path = Path(storage_path)
        self._personas: dict[str, Persona] = {}
        self._active_name: str = "Default"
        self._load()
    
    def _ensure_default(self) -> None:
        if "Default" not in self._personas:
            self._personas["Default"] = Persona(name="Default")
    
    def _load(self) -> None:
        self._personas = {}
        if self._storage_path.exists():
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, pdata in data.get("personas", {}).items():
                    self._personas[name] = Persona.from_dict(pdata)
                self._active_name = data.get("active", "Default")
            except Exception as e:
                logger.warning("Failed to load personas: %s", e)
        self._ensure_default()
        if self._active_name not in self._personas:
            self._active_name = "Default"
    
    def _save(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active": self._active_name,
            "personas": {name: p.to_dict() for name, p in self._personas.items()},
        }
        try:
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save personas: %s", e)
    
    def list_personas(self) -> list[str]:
        return sorted(self._personas.keys())
    
    def get_persona(self, name: str) -> Persona | None:
        return self._personas.get(name)
    
    def get_active_persona(self) -> Persona:
        return self._personas.get(self._active_name) or self._personas["Default"]
    
    def get_active_name(self) -> str:
        return self._active_name
    
    def set_active(self, name: str) -> bool:
        if name in self._personas:
            self._active_name = name
            self._save()
            return True
        return False
    
    def save_persona(self, persona: Persona) -> None:
        self._personas[persona.name] = persona
        self._save()
    
    def delete_persona(self, name: str) -> bool:
        if name == "Default":
            return False  # Cannot delete default
        if name in self._personas:
            del self._personas[name]
            if self._active_name == name:
                self._active_name = "Default"
            self._save()
            return True
        return False


# Parameter descriptions for UI
PARAMETER_DESCRIPTIONS = {
    "temperature": {
        "label": "Creativity (Temperature)",
        "description": "Controls randomness in responses.",
        "low": "More focused and deterministic",
        "high": "More creative and varied",
        "min": 0.0,
        "max": 2.0,
        "step": 0.1,
        "default": 0.7,
    },
    "top_p": {
        "label": "Response Diversity (Top P)",
        "description": "Nucleus sampling: considers tokens until cumulative probability reaches this value.",
        "low": "Narrower, more predictable word choices",
        "high": "Wider vocabulary, more diverse phrasing",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "default": 0.9,
    },
    "top_k": {
        "label": "Vocabulary Limit (Top K)",
        "description": "Limits consideration to the top K most likely tokens.",
        "low": "Very constrained vocabulary",
        "high": "Full vocabulary available (0 = unlimited)",
        "min": 0,
        "max": 100,
        "step": 5,
        "default": 40,
    },
    "repeat_penalty": {
        "label": "Repetition Penalty",
        "description": "Discourages the model from repeating itself.",
        "low": "May repeat phrases more often",
        "high": "Strongly avoids repetition",
        "min": 1.0,
        "max": 2.0,
        "step": 0.05,
        "default": 1.1,
    },
}
