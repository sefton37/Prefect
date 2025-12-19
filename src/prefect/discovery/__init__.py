"""Command discovery module for Prefect.

This module provides automatic discovery of Necesse server console commands
via paginated help parsing, with robust heuristics that don't assume format stability.
"""
from __future__ import annotations

from prefect.discovery.parser import normalize_output, parse_command_line, extract_commands_from_page
from prefect.discovery.registry import CommandEntry, CommandRegistry, DiscoverySnapshot
from prefect.discovery.discoverer import CommandDiscoverer
from prefect.discovery.bootstrap import AllowlistBootstrapper, AllowlistEntry, GeneratedAllowlist

__all__ = [
    "normalize_output",
    "parse_command_line",
    "extract_commands_from_page",
    "CommandEntry",
    "CommandRegistry",
    "DiscoverySnapshot",
    "CommandDiscoverer",
    "AllowlistBootstrapper",
    "AllowlistEntry",
    "GeneratedAllowlist",
]
