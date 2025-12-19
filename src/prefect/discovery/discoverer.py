"""Command discoverer - paginated help iteration with loop detection.

Discovers all console commands exposed by the running Necesse server.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prefect.discovery.parser import normalize_output, extract_commands_from_page
from prefect.discovery.registry import (
    CommandEntry,
    CommandRegistry,
    DiscoveryMetadata,
    DiscoverySnapshot,
    generate_snapshot_filename,
)

logger = logging.getLogger(__name__)


@dataclass
class ConsoleResult:
    """Result from executing a console command."""
    stdout: str
    exit_ok: bool
    timestamp: str


class CommandDiscoverer:
    """Discovers server commands via paginated help.
    
    Uses hash-based loop detection and configurable termination criteria.
    """
    
    def __init__(
        self,
        *,
        run_command: Callable[[str], ConsoleResult],
        server_root: Path,
        max_help_pages: int = 50,
        page_stable_limit: int = 2,
        help_cmd: str = "help",
        help_page_template: str = "help {page}",
        inter_page_delay: float = 0.5,
    ):
        """Initialize discoverer.
        
        Args:
            run_command: Callable that executes a console command and returns result.
            server_root: Path to the Necesse server root.
            max_help_pages: Hard cap on pages to attempt.
            page_stable_limit: Stop after this many consecutive pages with no new commands.
            help_cmd: Command for first help page.
            help_page_template: Template for subsequent pages (use {page}).
            inter_page_delay: Seconds to wait between page requests.
        """
        self._run_command = run_command
        self._server_root = server_root
        self._max_help_pages = max_help_pages
        self._page_stable_limit = page_stable_limit
        self._help_cmd = help_cmd
        self._help_page_template = help_page_template
        self._inter_page_delay = inter_page_delay
    
    def _compute_hash(self, text: str) -> str:
        """Compute hash of normalized text for loop detection."""
        normalized = normalize_output(text).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    
    def _is_empty_output(self, text: str) -> bool:
        """Check if output is effectively empty."""
        normalized = normalize_output(text).strip()
        if not normalized:
            return True
        # Also treat single-line noise as empty
        lines = [l for l in normalized.split("\n") if l.strip()]
        return len(lines) == 0
    
    def discover(self) -> DiscoverySnapshot:
        """Run discovery and return a complete snapshot.
        
        Returns:
            DiscoverySnapshot with all discovered commands and metadata.
        """
        snapshot = DiscoverySnapshot.create(self._server_root)
        registry = snapshot.commands
        metadata = snapshot.help
        
        seen_hashes: set[str] = set()
        stable_count = 0  # Pages with no new commands
        termination_reason = "max_pages_reached"
        
        # Page 1
        logger.info("Discovery: fetching help page 1")
        result = self._run_command(self._help_cmd)
        
        if not result.exit_ok:
            metadata.termination_reason = "help_command_failed"
            metadata.pages_attempted = 1
            logger.error("Discovery: help command failed")
            return snapshot
        
        page1_hash = self._compute_hash(result.stdout)
        seen_hashes.add(page1_hash)
        metadata.page_hashes[1] = page1_hash
        metadata.pages_attempted = 1
        
        if self._is_empty_output(result.stdout):
            metadata.termination_reason = "empty_help_output"
            logger.warning("Discovery: help returned empty output")
            return snapshot
        
        # Extract commands from page 1
        entries = extract_commands_from_page(result.stdout, page_number=1)
        new_count = 0
        for parsed, page_num in entries:
            entry = CommandEntry.from_parsed(parsed, page_num)
            if registry.add(entry):
                new_count += 1
                logger.debug("Discovery: found command '%s' on page 1", entry.key)
        
        metadata.pages_captured = 1
        
        if new_count == 0:
            stable_count += 1
        else:
            stable_count = 0
        
        logger.info("Discovery: page 1 yielded %d new commands (total: %d)", new_count, len(registry))
        
        # Iterate subsequent pages
        for page in range(2, self._max_help_pages + 1):
            time.sleep(self._inter_page_delay)
            
            cmd = self._help_page_template.format(page=page)
            logger.info("Discovery: fetching help page %d via '%s'", page, cmd)
            
            result = self._run_command(cmd)
            metadata.pages_attempted = page
            
            if not result.exit_ok:
                termination_reason = f"page_{page}_failed"
                logger.warning("Discovery: page %d command failed, stopping", page)
                break
            
            # Check for empty output
            if self._is_empty_output(result.stdout):
                termination_reason = "empty_page_output"
                logger.info("Discovery: page %d returned empty output, stopping", page)
                break
            
            # Check for hash loop (pagination cycling back)
            page_hash = self._compute_hash(result.stdout)
            if page_hash in seen_hashes:
                termination_reason = "pagination_loop_detected"
                logger.info("Discovery: page %d hash matches previous page, stopping", page)
                break
            
            seen_hashes.add(page_hash)
            metadata.page_hashes[page] = page_hash
            metadata.pages_captured = page
            
            # Extract commands
            entries = extract_commands_from_page(result.stdout, page_number=page)
            new_count = 0
            for parsed, page_num in entries:
                entry = CommandEntry.from_parsed(parsed, page_num)
                if registry.add(entry):
                    new_count += 1
                    logger.debug("Discovery: found command '%s' on page %d", entry.key, page)
            
            logger.info("Discovery: page %d yielded %d new commands (total: %d)", page, new_count, len(registry))
            
            # Check stable count
            if new_count == 0:
                stable_count += 1
                if stable_count >= self._page_stable_limit:
                    termination_reason = "stable_no_new_commands"
                    logger.info("Discovery: %d consecutive pages with no new commands, stopping", stable_count)
                    break
            else:
                stable_count = 0
        
        metadata.termination_reason = termination_reason
        logger.info(
            "Discovery complete: %d commands found, %d pages captured, reason=%s",
            len(registry),
            metadata.pages_captured,
            termination_reason,
        )
        
        return snapshot
    
    def discover_and_save(self, output_dir: Path) -> tuple[DiscoverySnapshot, Path]:
        """Run discovery and save snapshot to file.
        
        Args:
            output_dir: Directory to save snapshot (will be created if missing).
            
        Returns:
            Tuple of (snapshot, path_to_saved_file).
        """
        snapshot = self.discover()
        
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = generate_snapshot_filename()
        filepath = output_dir / filename
        
        snapshot.save(filepath)
        logger.info("Discovery snapshot saved to %s", filepath)
        
        return snapshot, filepath
