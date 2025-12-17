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

    @mcp.tool(name="prefect.summarize_recent_logs")
    async def summarize_recent_logs(n: int = 50) -> dict:
        return await core.summarize_recent_logs(n=n)

    return mcp, settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    mcp, settings = _build_server()

    transport = (settings.mcp_transport or "stdio").lower()
    if transport == "sse":
        # Best-effort: depending on MCP SDK version, args may differ.
        mcp.run(transport="sse", host=settings.mcp_host, port=settings.mcp_port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
