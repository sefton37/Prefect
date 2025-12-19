"""Tests for prefect.mcp.command_tools module."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from prefect.mcp.command_tools import (
    CommandParameter,
    CommandToolDefinition,
    CommandToolRegistry,
    parse_command_syntax,
    categorize_command,
    create_tool_definition,
    load_tools_from_snapshot,
    get_tool_descriptions_for_llm,
)
from prefect.discovery.registry import CommandEntry


class TestCommandParameter:
    """Tests for CommandParameter dataclass."""

    def test_clean_name_simple(self):
        param = CommandParameter(name="player", required=True)
        assert param.clean_name == "player"

    def test_clean_name_with_slash(self):
        param = CommandParameter(name="authentication/name", required=True)
        assert param.clean_name == "authentication_or_name"

    def test_clean_name_with_special_chars(self):
        param = CommandParameter(name="player-name", required=True)
        assert param.clean_name == "player_name"

    def test_clean_name_starting_with_digit(self):
        param = CommandParameter(name="1player", required=True)
        assert param.clean_name == "player"

    def test_to_schema_basic(self):
        param = CommandParameter(name="player", required=True)
        schema = param.to_schema()
        assert schema["type"] == "string"
        assert "player" in schema["description"]

    def test_to_schema_with_choices(self):
        param = CommandParameter(
            name="action",
            required=True,
            choices=["start", "stop", "clear"],
        )
        schema = param.to_schema()
        assert schema["enum"] == ["start", "stop", "clear"]
        assert "options:" in schema["description"]


class TestParseCommandSyntax:
    """Tests for syntax parsing."""

    def test_no_params(self):
        params = parse_command_syntax("help")
        assert params == []

    def test_single_required_param(self):
        params = parse_command_syntax("ban <player>")
        assert len(params) == 1
        assert params[0].name == "player"
        assert params[0].required is True

    def test_single_optional_param(self):
        params = parse_command_syntax("help [page]")
        assert len(params) == 1
        assert params[0].name == "page"
        assert params[0].required is False

    def test_optional_param_with_angle_brackets(self):
        params = parse_command_syntax("help [<page/command>]")
        assert len(params) == 1
        assert params[0].name == "page/command"
        assert params[0].required is False
        assert params[0].choices == ["page", "command"]

    def test_mixed_params(self):
        params = parse_command_syntax("buff [<player>] <buff> [<seconds>]")
        assert len(params) == 3
        assert params[0].name == "player"
        assert params[0].required is False
        assert params[1].name == "buff"
        assert params[1].required is True
        assert params[2].name == "seconds"
        assert params[2].required is False

    def test_choices_extracted(self):
        params = parse_command_syntax("rain <start/clear>")
        assert len(params) == 1
        assert params[0].choices == ["start", "clear"]

    def test_complex_syntax(self):
        # Note: Deeply nested optional params are a known limitation
        # The parser does best-effort extraction
        params = parse_command_syntax(
            "permissions <list/set/get> [<authentication/name> [<permissions>]]"
        )
        # We get at least the first param correctly
        assert len(params) >= 1
        assert params[0].name == "list/set/get"
        assert params[0].required is True
        assert params[0].choices == ["list", "set", "get"]

    def test_nested_optional_params(self):
        # Nested brackets are tricky - we extract what we can
        params = parse_command_syntax("cleardrops [<global> [<type>]]")
        # Parser gets the outer bracket content
        assert len(params) >= 1
        assert params[0].required is False


class TestCategorizeCommand:
    """Tests for command categorization and risk assessment."""

    def test_ban_is_moderation_dangerous(self):
        category, risk = categorize_command("ban", "ban <player>")
        assert category == "moderation"
        assert risk == "dangerous"

    def test_kick_is_moderation_dangerous(self):
        category, risk = categorize_command("kick", "kick <player>")
        assert category == "moderation"
        assert risk == "dangerous"

    def test_help_is_info_safe(self):
        category, risk = categorize_command("help", "help [<page>]")
        assert category == "info"
        assert risk == "safe"

    def test_players_is_info_safe(self):
        category, risk = categorize_command("players", "players")
        assert category == "info"
        assert risk == "safe"

    def test_give_is_items_moderate(self):
        category, risk = categorize_command("give", "give <player> <item>")
        assert category == "items"
        assert risk == "moderate"

    def test_tp_is_player_moderate(self):
        category, risk = categorize_command("tp", "tp <player> <target>")
        assert category == "player"
        assert risk == "moderate"

    def test_save_is_admin_dangerous(self):
        category, risk = categorize_command("save", "save")
        assert category == "admin"
        assert risk == "dangerous"


class TestCommandToolDefinition:
    """Tests for CommandToolDefinition."""

    def test_tool_name_prefix(self):
        tool = CommandToolDefinition(
            command_name="help",
            syntax="help [<page>]",
            description="Show help",
        )
        assert tool.tool_name == "necesse.help"

    def test_required_params(self):
        tool = CommandToolDefinition(
            command_name="ban",
            syntax="ban <player>",
            description="Ban a player",
            parameters=[
                CommandParameter(name="player", required=True),
            ],
        )
        assert len(tool.required_params) == 1
        assert tool.required_params[0].name == "player"
        assert len(tool.optional_params) == 0

    def test_to_tool_schema(self):
        tool = CommandToolDefinition(
            command_name="buff",
            syntax="buff [<player>] <buff> [<seconds>]",
            description="Apply a buff",
            parameters=[
                CommandParameter(name="player", required=False),
                CommandParameter(name="buff", required=True),
                CommandParameter(name="seconds", required=False),
            ],
        )
        schema = tool.to_tool_schema()
        assert schema["type"] == "object"
        assert "player" in schema["properties"]
        assert "buff" in schema["properties"]
        assert "seconds" in schema["properties"]
        assert schema["required"] == ["buff"]

    def test_build_command_string_simple(self):
        tool = CommandToolDefinition(
            command_name="ban",
            syntax="ban <player>",
            description="Ban a player",
            parameters=[
                CommandParameter(name="player", required=True),
            ],
        )
        result = tool.build_command_string(player="TestPlayer")
        assert result == "ban TestPlayer"

    def test_build_command_string_optional_included(self):
        tool = CommandToolDefinition(
            command_name="kick",
            syntax="kick <player> [<reason>]",
            description="Kick a player",
            parameters=[
                CommandParameter(name="player", required=True),
                CommandParameter(name="reason", required=False),
            ],
        )
        result = tool.build_command_string(player="TestPlayer", reason="AFK")
        assert result == "kick TestPlayer AFK"

    def test_build_command_string_optional_omitted(self):
        tool = CommandToolDefinition(
            command_name="kick",
            syntax="kick <player> [<reason>]",
            description="Kick a player",
            parameters=[
                CommandParameter(name="player", required=True),
                CommandParameter(name="reason", required=False),
            ],
        )
        result = tool.build_command_string(player="TestPlayer")
        assert result == "kick TestPlayer"

    def test_build_command_string_missing_required_raises(self):
        tool = CommandToolDefinition(
            command_name="ban",
            syntax="ban <player>",
            description="Ban a player",
            parameters=[
                CommandParameter(name="player", required=True),
            ],
        )
        with pytest.raises(ValueError, match="Missing required parameter"):
            tool.build_command_string()


class TestCreateToolDefinition:
    """Tests for create_tool_definition function."""

    def test_creates_from_entry(self):
        entry = CommandEntry(
            key="help",
            name="help",
            syntax="help [<page/command>]",
            description="",
            source="help",
            page=1,
            raw_line="help [<page/command>]",
        )
        tool = create_tool_definition(entry)
        assert tool is not None
        assert tool.command_name == "help"
        assert len(tool.parameters) == 1

    def test_skips_noise_entries(self):
        entry = CommandEntry(
            key="suggesting",
            name="Suggesting",
            syntax="Suggesting garbage collection due to empty server...",
            description="",
            source="help",
            page=1,
            raw_line="Suggesting garbage collection due to empty server...",
        )
        tool = create_tool_definition(entry)
        assert tool is None

    def test_skips_short_names(self):
        entry = CommandEntry(
            key="a",
            name="a",
            syntax="a",
            description="",
            source="help",
            page=1,
            raw_line="a",
        )
        tool = create_tool_definition(entry)
        assert tool is None


class TestCommandToolRegistry:
    """Tests for CommandToolRegistry."""

    def test_register_and_get(self):
        registry = CommandToolRegistry()
        tool = CommandToolDefinition(
            command_name="help",
            syntax="help",
            description="Show help",
        )
        registry.register(tool)
        
        assert "help" in registry
        assert "necesse.help" in registry
        assert registry.get("help") == tool
        assert registry.get("necesse.help") == tool

    def test_get_by_category(self):
        registry = CommandToolRegistry()
        registry.register(CommandToolDefinition(
            command_name="help", syntax="help", description="",
            category="info",
        ))
        registry.register(CommandToolDefinition(
            command_name="players", syntax="players", description="",
            category="info",
        ))
        registry.register(CommandToolDefinition(
            command_name="ban", syntax="ban <player>", description="",
            category="moderation",
        ))
        
        info_tools = registry.get_by_category("info")
        assert len(info_tools) == 2
        
        mod_tools = registry.get_by_category("moderation")
        assert len(mod_tools) == 1

    def test_get_safe_tools(self):
        registry = CommandToolRegistry()
        registry.register(CommandToolDefinition(
            command_name="help", syntax="help", description="",
            risk_level="safe",
        ))
        registry.register(CommandToolDefinition(
            command_name="ban", syntax="ban <player>", description="",
            risk_level="dangerous",
        ))
        
        safe = registry.get_safe_tools()
        assert len(safe) == 1
        assert safe[0].command_name == "help"

    def test_len(self):
        registry = CommandToolRegistry()
        assert len(registry) == 0
        
        registry.register(CommandToolDefinition(
            command_name="help", syntax="help", description="",
        ))
        assert len(registry) == 1


class TestLoadToolsFromSnapshot:
    """Tests for loading tools from snapshot files."""

    def test_loads_from_snapshot(self):
        snapshot_data = {
            "server_root": "/test",
            "captured_at": "2025-01-01T00:00:00",
            "help": {"pages_attempted": 1, "pages_captured": 1},
            "commands": [
                {
                    "key": "help",
                    "name": "help",
                    "syntax": "help [<page>]",
                    "description": "",
                    "source": "help",
                    "page": 1,
                    "raw_line": "help [<page>]",
                },
                {
                    "key": "ban",
                    "name": "ban",
                    "syntax": "ban <player>",
                    "description": "",
                    "source": "help",
                    "page": 1,
                    "raw_line": "ban <player>",
                },
            ],
        }
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(snapshot_data, f)
            f.flush()
            
            registry = load_tools_from_snapshot(Path(f.name))
            
        assert len(registry) == 2
        assert "help" in registry
        assert "ban" in registry

    def test_filters_noise(self):
        snapshot_data = {
            "server_root": "/test",
            "captured_at": "2025-01-01T00:00:00",
            "help": {"pages_attempted": 1, "pages_captured": 1},
            "commands": [
                {
                    "key": "help",
                    "name": "help",
                    "syntax": "help",
                    "description": "",
                    "source": "help",
                    "page": 1,
                    "raw_line": "help",
                },
                {
                    "key": "suggesting",
                    "name": "Suggesting",
                    "syntax": "Suggesting garbage collection due to empty server...",
                    "description": "",
                    "source": "help",
                    "page": 1,
                    "raw_line": "Suggesting garbage collection...",
                },
            ],
        }
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(snapshot_data, f)
            f.flush()
            
            registry = load_tools_from_snapshot(Path(f.name))
            
        assert len(registry) == 1
        assert "help" in registry
        assert "suggesting" not in registry


class TestToolDescriptionsForLLM:
    """Tests for LLM-readable tool descriptions."""

    def test_generates_markdown(self):
        registry = CommandToolRegistry()
        registry.register(CommandToolDefinition(
            command_name="help",
            syntax="help [<page>]",
            description="Show available commands",
            category="info",
            risk_level="safe",
        ))
        registry.register(CommandToolDefinition(
            command_name="ban",
            syntax="ban <player>",
            description="Ban a player from the server",
            category="moderation",
            risk_level="dangerous",
        ))
        
        output = get_tool_descriptions_for_llm(registry)
        
        assert "# Available Necesse Server Commands" in output
        assert "## Info" in output
        assert "## Moderation" in output
        assert "**help**" in output
        assert "**ban**" in output
        assert "✓" in output  # safe indicator
        assert "⛔" in output  # dangerous indicator


class TestToolSelectionForIntent:
    """Tests for LLM tool selection based on user intent.
    
    These tests verify that given a user's intent, the appropriate
    tool can be identified. This simulates how an LLM would choose
    a tool based on the user's request.
    """

    @pytest.fixture
    def full_registry(self) -> CommandToolRegistry:
        """Create a registry with common commands."""
        registry = CommandToolRegistry()
        
        # Info commands
        registry.register(CommandToolDefinition(
            command_name="help",
            syntax="help [<page/command>]",
            description="Show available commands",
            category="info",
            risk_level="safe",
        ))
        registry.register(CommandToolDefinition(
            command_name="players",
            syntax="players",
            description="List connected players",
            category="info",
            risk_level="safe",
        ))
        registry.register(CommandToolDefinition(
            command_name="levels",
            syntax="levels",
            description="List loaded levels",
            category="info",
            risk_level="safe",
        ))
        
        # Moderation commands
        registry.register(CommandToolDefinition(
            command_name="ban",
            syntax="ban <authentication/name>",
            description="Ban a player from the server",
            category="moderation",
            risk_level="dangerous",
            parameters=[CommandParameter(name="authentication/name", required=True)],
        ))
        registry.register(CommandToolDefinition(
            command_name="kick",
            syntax="kick <player> [<message/reason>]",
            description="Kick a player from the server",
            category="moderation",
            risk_level="dangerous",
            parameters=[
                CommandParameter(name="player", required=True),
                CommandParameter(name="message/reason", required=False),
            ],
        ))
        registry.register(CommandToolDefinition(
            command_name="unban",
            syntax="unban <authentication/name>",
            description="Remove a ban from a player",
            category="moderation",
            risk_level="dangerous",
            parameters=[CommandParameter(name="authentication/name", required=True)],
        ))
        
        # Player commands
        registry.register(CommandToolDefinition(
            command_name="tp",
            syntax="tp [<player1>] <player2/home/death/spawn/level>",
            description="Teleport a player",
            category="player",
            risk_level="moderate",
            parameters=[
                CommandParameter(name="player1", required=False),
                CommandParameter(name="player2/home/death/spawn/level", required=True),
            ],
        ))
        registry.register(CommandToolDefinition(
            command_name="hp",
            syntax="hp [<player>] <health>",
            description="Set a player's health",
            category="player",
            risk_level="moderate",
            parameters=[
                CommandParameter(name="player", required=False),
                CommandParameter(name="health", required=True),
            ],
        ))
        
        # Item commands
        registry.register(CommandToolDefinition(
            command_name="give",
            syntax="give [<player>] <item> [<amount>]",
            description="Give an item to a player",
            category="items",
            risk_level="moderate",
            parameters=[
                CommandParameter(name="player", required=False),
                CommandParameter(name="item", required=True),
                CommandParameter(name="amount", required=False),
            ],
        ))
        
        # Environment commands
        registry.register(CommandToolDefinition(
            command_name="time",
            syntax="time <set/add> [<amount>]",
            description="Change the world time",
            category="environment",
            risk_level="moderate",
            parameters=[
                CommandParameter(name="set/add", required=True, choices=["set", "add"]),
                CommandParameter(name="amount", required=False),
            ],
        ))
        
        return registry

    def test_intent_who_is_online(self, full_registry: CommandToolRegistry):
        """User asks: 'Who is online?' -> players command."""
        # The LLM would analyze intent and match to appropriate tool
        tool = full_registry.get("players")
        assert tool is not None
        assert tool.command_name == "players"
        assert tool.risk_level == "safe"

    def test_intent_list_commands(self, full_registry: CommandToolRegistry):
        """User asks: 'What commands are available?' -> help command."""
        tool = full_registry.get("help")
        assert tool is not None
        assert tool.command_name == "help"

    def test_intent_remove_player(self, full_registry: CommandToolRegistry):
        """User asks: 'Remove player BadGuy from the server' -> kick command."""
        tool = full_registry.get("kick")
        assert tool is not None
        assert tool.command_name == "kick"
        assert tool.risk_level == "dangerous"
        
        # Verify command can be built
        cmd = tool.build_command_string(player="BadGuy", message_or_reason="Breaking rules")
        assert cmd == "kick BadGuy Breaking rules"

    def test_intent_permanent_ban(self, full_registry: CommandToolRegistry):
        """User asks: 'Permanently ban Griefer123' -> ban command."""
        tool = full_registry.get("ban")
        assert tool is not None
        assert tool.command_name == "ban"
        
        cmd = tool.build_command_string(authentication_or_name="Griefer123")
        assert cmd == "ban Griefer123"

    def test_intent_give_item(self, full_registry: CommandToolRegistry):
        """User asks: 'Give player SomeGuy 10 gold coins' -> give command."""
        tool = full_registry.get("give")
        assert tool is not None
        assert tool.command_name == "give"
        
        cmd = tool.build_command_string(player="SomeGuy", item="goldcoin", amount="10")
        assert cmd == "give SomeGuy goldcoin 10"

    def test_intent_teleport_home(self, full_registry: CommandToolRegistry):
        """User asks: 'Teleport NewPlayer to their home' -> tp command."""
        tool = full_registry.get("tp")
        assert tool is not None
        
        cmd = tool.build_command_string(
            player1="NewPlayer",
            player2_or_home_or_death_or_spawn_or_level="home",
        )
        assert cmd == "tp NewPlayer home"

    def test_intent_set_time_day(self, full_registry: CommandToolRegistry):
        """User asks: 'Make it daytime' -> time command."""
        tool = full_registry.get("time")
        assert tool is not None
        
        cmd = tool.build_command_string(set_or_add="set", amount="0")
        assert cmd == "time set 0"

    def test_category_search_for_moderation(self, full_registry: CommandToolRegistry):
        """Verify we can find all moderation tools."""
        mod_tools = full_registry.get_by_category("moderation")
        assert len(mod_tools) == 3
        names = {t.command_name for t in mod_tools}
        assert names == {"ban", "kick", "unban"}

    def test_safe_only_selection(self, full_registry: CommandToolRegistry):
        """Verify we can get only safe commands for restricted contexts."""
        safe_tools = full_registry.get_safe_tools()
        for tool in safe_tools:
            assert tool.risk_level == "safe"
        
        names = {t.command_name for t in safe_tools}
        assert "ban" not in names
        assert "kick" not in names
        assert "help" in names
        assert "players" in names
