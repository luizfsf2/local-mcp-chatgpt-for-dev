"""Local stdio MCP server that brokers allowlisted Herdr Core sessions.

Start through `python server.py`, or let the official tunnel-client start it.
"""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from herdr_bridge import Bridge

CONFIG = Path(os.environ.get("HERDR_BRIDGE_CONFIG", str(Path(__file__).with_name("config.json"))))
bridge = Bridge(CONFIG)
mcp = FastMCP("Herdr Agent Bridge")


@mcp.tool()
def list_projects() -> list[dict]:
    """List only projects explicitly authorized in the local config."""
    return bridge.list_projects()


@mcp.tool()
def list_sessions() -> list[dict]:
    """List authorized Claude/Codex Herdr Core terminals, including finished ones."""
    return bridge.list_sessions()


@mcp.tool()
def get_session(session: str) -> dict:
    """Read the state/status for a listed session. Unknown is not idle or done."""
    return bridge.get_session(session)


@mcp.tool()
def read_session(session: str, lines: int = 60) -> dict:
    """Read visible screen plus limited history (0..200 lines) for an authorized session."""
    return bridge.read_session(session, lines)


@mcp.tool()
def start_agent(project: str, agent: str) -> dict:
    """Start a persistent Claude Code or Codex CLI in an allowlisted local project."""
    return bridge.start_agent(project, agent)


@mcp.tool()
def send_message(session: str, message: str) -> dict:
    """Send natural-language instructions to a CONFIRMED Claude/Codex CLI, never to an unverified shell. No --force."""
    return bridge.send_message(session, message)


@mcp.tool()
def wait_session(session: str, seconds: int = 8) -> dict:
    """Wait up to 15 seconds for a session status change; timeout is not completion."""
    return bridge.wait_session(session, seconds)


@mcp.tool()
def interrupt_session(session: str) -> dict:
    """Request interruption of a confirmed agent. Disabled by default in local config."""
    return bridge.interrupt_session(session)


if __name__ == "__main__":
    mcp.run(transport="stdio")
