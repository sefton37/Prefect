from __future__ import annotations

import logging

from prefect.config import get_settings
from prefect.mcp.tools import PrefectCore

logger = logging.getLogger(__name__)


def _build_server():
    # Using the standard MCP Python SDK (mcp) if installed.
    from mcp.server.fastmcp import FastMCP

    settings = get_settings()
    core = PrefectCore(settings)
    core.start()

    mcp = FastMCP("prefect-necesse")

    # Dynamically create one tool per configured command.
    # Each tool takes a single free-form args string (kept small by sanitizer limits).
    for cmd in core.command_names:
        tool_name = f"prefect.cmd.{cmd}"

        def _make(cmd_name: str):
            @mcp.tool(name=f"prefect.cmd.{cmd_name}")
            def _tool(args: str = "") -> dict:
                full = cmd_name if not args.strip() else f"{cmd_name} {args.strip()}"
                return core.run_command(full)

            return _tool

        _make(cmd)

    @mcp.tool(name="prefect.get_status")
    def get_status() -> dict:
        return core.get_status()

    @mcp.tool(name="prefect.get_recent_logs")
    def get_recent_logs(n: int) -> list[str]:
        return core.get_recent_logs(n)

    @mcp.tool(name="prefect.run_command")
    def run_command(command: str) -> dict:
        return core.run_command(command)

    @mcp.tool(name="prefect.announce")
    def announce(message: str) -> dict:
        return core.announce(message)

    @mcp.tool(name="prefect.startup_reply")
    def startup_reply(reply: str) -> dict:
        return core.startup_reply(reply)

    @mcp.tool(name="prefect.wait_until_ready")
    def wait_until_ready(timeout_seconds: float = 60.0) -> dict:
        return core.wait_until_ready(timeout_seconds=timeout_seconds)

    @mcp.tool(name="prefect.summarize_recent_logs")
    async def summarize_recent_logs(n: int = 50) -> dict:
        return await core.summarize_recent_logs(n=n)

    return mcp, settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    mcp, settings = _build_server()

    transport = (settings.mcp_transport or "stdio").lower()
    logger.info(
        "Prefect MCP starting transport=%s server_root=%s control_mode=%s",
        transport,
        settings.server_root,
        settings.control_mode,
    )
    if transport == "sse":
        # Best-effort: depending on MCP SDK version, args may differ.
        logger.info("Prefect MCP SSE listening on http://%s:%s", settings.mcp_host, settings.mcp_port)
        mcp.run(transport="sse", host=settings.mcp_host, port=settings.mcp_port)
    else:
        logger.info("Prefect MCP stdio ready (waiting for MCP client)")
        mcp.run()


if __name__ == "__main__":
    main()
