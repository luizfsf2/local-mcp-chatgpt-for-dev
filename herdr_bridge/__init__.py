"""An allowlisted bridge between an MCP server and Herdr Core (hc)."""

from .core import Bridge, BridgeError, run_hc

__all__ = ["Bridge", "BridgeError", "run_hc"]
