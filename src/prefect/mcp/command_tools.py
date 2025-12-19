"""Dynamic MCP tool generation from discovered Necesse server commands.

Parses command syntax to extract parameters and generates tool definitions
that an LLM can choose from based on user intent.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prefect.discovery.registry import CommandEntry

logger = logging.getLogger(__name__)

# Pattern to extract parameters from command syntax
# Matches: <required>, [optional], [<optionalNamed>], <name/with/options>
_PARAM_PATTERN = re.compile(
    r"""
    \[<([^>]+)>\]  |  # [<optionalNamed>] - optional with angle brackets
    <([^>]+)>      |  # <required> - required parameter
    \[([^\]]+)\]      # [optional] - optional parameter
    """,
    re.VERBOSE,
)


@dataclass
class CommandParameter:
    """A parameter extracted from command syntax."""
    
    name: str
    required: bool
    choices: list[str] = field(default_factory=list)
    description: str = ""
    
    @property
    def clean_name(self) -> str:
        """Parameter name suitable for use as a function argument."""
        # Convert "player1" -> "player1", "message/reason" -> "message_or_reason"
        name = self.name.replace("/", "_or_")
        name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        # Remove leading digits
        name = re.sub(r"^[0-9]+", "", name)
        return name or "arg"
    
    def to_schema(self) -> dict[str, Any]:
        """Convert to JSON schema property definition."""
        schema: dict[str, Any] = {
            "type": "string",
            "description": self.description or f"The {self.name} parameter",
        }
        if self.choices:
            schema["enum"] = self.choices
            schema["description"] += f" (options: {', '.join(self.choices)})"
        return schema


@dataclass
class CommandToolDefinition:
    """A tool definition generated from a discovered command."""
    
    command_name: str
    syntax: str
    description: str
    parameters: list[CommandParameter] = field(default_factory=list)
    category: str = "general"
    risk_level: str = "unknown"  # safe, moderate, dangerous
    
    @property
    def tool_name(self) -> str:
        """MCP tool name (prefixed with necesse.)."""
        return f"necesse.{self.command_name}"
    
    @property
    def required_params(self) -> list[CommandParameter]:
        """List of required parameters."""
        return [p for p in self.parameters if p.required]
    
    @property
    def optional_params(self) -> list[CommandParameter]:
        """List of optional parameters."""
        return [p for p in self.parameters if not p.required]
    
    def to_tool_schema(self) -> dict[str, Any]:
        """Generate the JSON schema for this tool's input."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        
        for param in self.parameters:
            properties[param.clean_name] = param.to_schema()
            if param.required:
                required.append(param.clean_name)
        
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    
    def build_command_string(self, **kwargs: str) -> str:
        """Build the actual command string from provided arguments.
        
        Args:
            **kwargs: Parameter values keyed by clean_name
            
        Returns:
            The command string ready to send to the server
        """
        parts = [self.command_name]
        
        for param in self.parameters:
            value = kwargs.get(param.clean_name)
            if value is not None:
                parts.append(str(value))
            elif param.required:
                raise ValueError(f"Missing required parameter: {param.name}")
        
        return " ".join(parts)


def parse_command_syntax(syntax: str) -> list[CommandParameter]:
    """Parse command syntax string to extract parameters.
    
    Examples:
        "help [<page/command>]" -> [CommandParameter(name="page/command", required=False, choices=["page", "command"])]
        "ban <authentication/name>" -> [CommandParameter(name="authentication/name", required=True)]
        "buff [<player>] <buff> [<seconds>]" -> 3 parameters
    
    Args:
        syntax: The command syntax string from help output
        
    Returns:
        List of extracted CommandParameter objects
    """
    params: list[CommandParameter] = []
    
    # Find all parameter matches
    for match in _PARAM_PATTERN.finditer(syntax):
        optional_named = match.group(1)  # [<name>]
        required = match.group(2)         # <name>
        optional_bare = match.group(3)    # [name]
        
        if optional_named:
            name = optional_named
            is_required = False
        elif required:
            name = required
            is_required = True
        elif optional_bare:
            name = optional_bare
            is_required = False
        else:
            continue
        
        # Extract choices if name contains /
        choices: list[str] = []
        if "/" in name:
            choices = [c.strip() for c in name.split("/")]
        
        params.append(CommandParameter(
            name=name,
            required=is_required,
            choices=choices,
        ))
    
    return params


def categorize_command(name: str, syntax: str) -> tuple[str, str]:
    """Categorize a command and assess its risk level.
    
    Returns:
        Tuple of (category, risk_level)
    """
    name_lower = name.lower()
    syntax_lower = syntax.lower()
    
    # Risk assessment
    dangerous_keywords = {
        "ban", "kick", "delete", "clear", "regen", "die", "save",
        "password", "permissions", "op", "deop", "unban",
    }
    moderate_keywords = {
        "give", "buff", "hp", "mana", "spawn", "tp", "set", "time",
        "rain", "raid", "enchant", "upgrade",
    }
    
    if name_lower in dangerous_keywords or any(k in name_lower for k in dangerous_keywords):
        risk = "dangerous"
    elif name_lower in moderate_keywords or any(k in name_lower for k in moderate_keywords):
        risk = "moderate"
    else:
        risk = "safe"
    
    # Category assignment (order matters - more specific matches first)
    if any(k in name_lower for k in ["ban", "kick", "unban", "permissions", "op"]):
        category = "moderation"
    elif name_lower in {"players", "playernames"}:
        category = "info"
    elif any(k in name_lower for k in ["player", "tp", "hp", "mana", "buff", "hunger"]):
        category = "player"
    elif any(k in name_lower for k in ["spawn", "mob", "raid", "event"]):
        category = "world"
    elif any(k in name_lower for k in ["team", "invite"]):
        category = "team"
    elif any(k in name_lower for k in ["save", "settings", "password", "motd"]):
        category = "admin"
    elif any(k in name_lower for k in ["give", "item", "armor", "enchant", "upgrade"]):
        category = "items"
    elif any(k in name_lower for k in ["time", "rain", "difficulty"]):
        category = "environment"
    elif any(k in name_lower for k in ["help", "players", "levels", "network"]):
        category = "info"
    else:
        category = "general"
    
    return category, risk


def create_tool_definition(entry: "CommandEntry") -> CommandToolDefinition | None:
    """Create a tool definition from a CommandEntry.
    
    Args:
        entry: A discovered command entry
        
    Returns:
        CommandToolDefinition or None if the entry is invalid
    """
    # Skip noise entries
    if not entry.name or len(entry.name) < 2:
        return None
    
    # Skip entries that look like log messages (start with capital letter)
    # Real commands are lowercase (e.g., "help", "ban", "kick")
    if entry.name[0].isupper():
        return None
    
    # Skip if name contains invalid characters for a command
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9]*$", entry.name):
        return None
    
    params = parse_command_syntax(entry.syntax)
    category, risk = categorize_command(entry.name, entry.syntax)
    
    return CommandToolDefinition(
        command_name=entry.name,
        syntax=entry.syntax,
        description=entry.description or f"Execute the {entry.name} command",
        parameters=params,
        category=category,
        risk_level=risk,
    )


class CommandToolRegistry:
    """Registry of command tools generated from discovered commands."""
    
    def __init__(self) -> None:
        self._tools: dict[str, CommandToolDefinition] = {}
    
    def register(self, tool: CommandToolDefinition) -> None:
        """Register a tool definition."""
        self._tools[tool.tool_name] = tool
    
    def get(self, name: str) -> CommandToolDefinition | None:
        """Get a tool by name (with or without necesse. prefix)."""
        if not name.startswith("necesse."):
            name = f"necesse.{name}"
        return self._tools.get(name)
    
    def get_by_category(self, category: str) -> list[CommandToolDefinition]:
        """Get all tools in a category."""
        return [t for t in self._tools.values() if t.category == category]
    
    def get_safe_tools(self) -> list[CommandToolDefinition]:
        """Get all tools marked as safe."""
        return [t for t in self._tools.values() if t.risk_level == "safe"]
    
    def all_tools(self) -> list[CommandToolDefinition]:
        """Get all registered tools."""
        return list(self._tools.values())
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        if not name.startswith("necesse."):
            name = f"necesse.{name}"
        return name in self._tools


def load_tools_from_snapshot(snapshot_path: Path) -> CommandToolRegistry:
    """Load command tools from a discovery snapshot file.
    
    Args:
        snapshot_path: Path to the JSON snapshot file
        
    Returns:
        CommandToolRegistry populated with tools
    """
    import json
    from prefect.discovery.registry import CommandEntry
    
    registry = CommandToolRegistry()
    
    with open(snapshot_path) as f:
        data = json.load(f)
    
    commands = data.get("commands", [])
    
    for cmd_data in commands:
        entry = CommandEntry.from_dict(cmd_data)
        tool = create_tool_definition(entry)
        if tool:
            registry.register(tool)
            logger.debug(f"Registered tool: {tool.tool_name} ({tool.category}, {tool.risk_level})")
        else:
            logger.debug(f"Skipped invalid entry: {cmd_data.get('name', 'unknown')}")
    
    logger.info(f"Loaded {len(registry)} command tools from snapshot")
    return registry


def get_tool_descriptions_for_llm(registry: CommandToolRegistry) -> str:
    """Generate a formatted description of all tools for LLM context.
    
    This provides the LLM with information about available commands
    to help it choose the right one.
    """
    lines = ["# Available Necesse Server Commands\n"]
    
    # Group by category
    categories: dict[str, list[CommandToolDefinition]] = {}
    for tool in registry.all_tools():
        if tool.category not in categories:
            categories[tool.category] = []
        categories[tool.category].append(tool)
    
    for category in sorted(categories.keys()):
        lines.append(f"\n## {category.title()}\n")
        tools = sorted(categories[category], key=lambda t: t.command_name)
        
        for tool in tools:
            risk_indicator = {"safe": "✓", "moderate": "⚠", "dangerous": "⛔"}.get(
                tool.risk_level, "?"
            )
            lines.append(f"- **{tool.command_name}** {risk_indicator}: `{tool.syntax}`")
            if tool.description:
                lines.append(f"  {tool.description}")
    
    return "\n".join(lines)
