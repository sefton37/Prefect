"""Command line parser with robust heuristics for Necesse help output.

Does NOT assume stable formatting. Parses conservatively and stores raw lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ANSI escape sequence pattern (including partial codes without \x1b prefix)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")
# Also match bare [NNm] patterns that may appear without escape prefix
_ANSI_BARE = re.compile(r"\[\d+m")

# Timestamp pattern: [YYYY-MM-DD HH:MM:SS] or similar
_TIMESTAMP = re.compile(r"^\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\]\s*")

# Patterns for identifying command lines (conservative heuristics)
# Pattern 1: Starts with / or ! (chat-style commands)
_RE_CHAT_STYLE = re.compile(r"^[/!]([a-zA-Z][a-zA-Z0-9_-]*)")

# Pattern 2: Starts with alphanumeric token followed by space, dash, colon, or end
_RE_SIMPLE_CMD = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*(?:[-:\s]|$)")

# Pattern 3: Contains token followed by angle brackets or square brackets (syntax hints)
_RE_WITH_ARGS = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*[<\[]")

# Pattern 4: Line that looks like "command - description" or "command: description"
_RE_CMD_DESC = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*[-:]\s*\S")

# Noise patterns - lines to skip
_NOISE_PATTERNS = [
    re.compile(r"^-+$"),  # Separator lines
    re.compile(r"^=+$"),
    re.compile(r"^\s*$"),  # Empty lines
    re.compile(r"^page\s+\d+", re.IGNORECASE),  # Page indicators
    re.compile(r"^\d+\s*(/|of)\s*\d+", re.IGNORECASE),  # Page numbers like "1/5" or "1 of 5"
    re.compile(r"^type\s+.*(help|more)", re.IGNORECASE),  # "Type help for more"
    re.compile(r"^available\s+commands", re.IGNORECASE),  # Headers
    re.compile(r"^commands?\s*(page)?\s*\d*", re.IGNORECASE),  # "Commands page 1 of 16"
    re.compile(r"^server\s+commands", re.IGNORECASE),
    re.compile(r"^>\s"),  # Command echo like "> help"
    # Server log messages that slip through timestamps
    # Match "Suggesting garbage collection..." pattern (capitalized word + action + ellipsis)
    re.compile(r"^[A-Z][a-z]+ing\s+.*\.\.\.$"),
]


@dataclass
class ParsedCommand:
    """A command parsed from a help line."""
    name: str
    syntax: str
    description: str
    raw_line: str
    
    @property
    def key(self) -> str:
        """Normalized de-duplication key (lowercase, no leading slash)."""
        return self.name.lstrip("/!").lower()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    text = _ANSI_ESCAPE.sub("", text)
    text = _ANSI_BARE.sub("", text)  # Also strip bare [39m] style codes
    return text


def strip_timestamp(text: str) -> str:
    """Remove leading timestamp from text."""
    return _TIMESTAMP.sub("", text)


def normalize_output(text: str) -> str:
    """Normalize console output for parsing.
    
    - Remove ANSI escapes
    - Normalize line endings
    - Strip trailing whitespace per line
    - Preserve original spacing within lines (for raw storage)
    """
    text = strip_ansi(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def normalize_line_for_parsing(line: str) -> str:
    """Fully normalize a line for command parsing.
    
    - Strip ANSI codes
    - Strip timestamp prefix
    - Strip whitespace
    """
    line = strip_ansi(line)
    line = strip_timestamp(line)
    return line.strip()


def is_noise_line(line: str) -> bool:
    """Check if a line is noise (headers, separators, page indicators)."""
    stripped = line.strip()
    if not stripped:
        return True
    for pattern in _NOISE_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def parse_command_line(line: str) -> ParsedCommand | None:
    """Parse a single line to extract command information.
    
    Returns None if the line doesn't appear to be a command entry.
    Uses conservative heuristics - better to miss some than to hallucinate.
    """
    raw_line = line  # Preserve original for storage
    
    # Fully normalize for parsing (strip ANSI, timestamp, whitespace)
    stripped = normalize_line_for_parsing(line)
    if not stripped or is_noise_line(stripped):
        return None
    
    name: str | None = None
    syntax: str = ""
    description: str = ""
    
    # Try chat-style first (/command or !command)
    m = _RE_CHAT_STYLE.match(stripped)
    if m:
        name = stripped[0] + m.group(1)  # Keep the / or !
        remainder = stripped[len(name):].strip()
    else:
        # Try simple command pattern
        m = _RE_SIMPLE_CMD.match(stripped)
        if m:
            name = m.group(1)
            remainder = stripped[len(name):].strip()
        else:
            # Try pattern with args
            m = _RE_WITH_ARGS.match(stripped)
            if m:
                name = m.group(1)
                remainder = stripped[len(name):].strip()
            else:
                # Try command-description pattern
                m = _RE_CMD_DESC.match(stripped)
                if m:
                    name = m.group(1)
                    remainder = stripped[len(name):].strip()
    
    if not name:
        return None
    
    # Validate name looks reasonable
    if len(name.lstrip("/!")) < 2 or len(name) > 32:
        return None
    
    # Parse remainder into syntax and description
    # Common patterns:
    # "command <arg> [opt] - description"
    # "command <arg> [opt]: description"
    # "command - description"
    # "command <arg>"
    
    remainder = remainder.lstrip("-: ")
    
    # Look for description separator
    desc_match = re.search(r"\s+[-:]\s+(.+)$", remainder)
    if desc_match:
        description = desc_match.group(1).strip()
        syntax_part = remainder[:desc_match.start()].strip()
    else:
        syntax_part = remainder
        description = ""
    
    # Build full syntax
    if syntax_part:
        syntax = f"{name} {syntax_part}"
    else:
        syntax = name
    
    return ParsedCommand(
        name=name,
        syntax=syntax,
        description=description,
        raw_line=raw_line,
    )


def extract_commands_from_page(page_text: str, page_number: int = 1) -> list[tuple[ParsedCommand, int]]:
    """Extract all commands from a help page.
    
    Returns list of (ParsedCommand, page_number) tuples.
    """
    results: list[tuple[ParsedCommand, int]] = []
    
    normalized = normalize_output(page_text)
    
    for line in normalized.split("\n"):
        parsed = parse_command_line(line)
        if parsed is not None:
            results.append((parsed, page_number))
    
    return results
