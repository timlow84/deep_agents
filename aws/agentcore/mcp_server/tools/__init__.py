"""Tool module registry.

To add a new set of API tools:
  1. Create tools/your_api.py  (copy api_tools.py as a template)
  2. Import and call its register() function here

The MCP server calls register_all(mcp) once at startup.
"""

from .api_tools import register as _register_api_tools
from .carpark_api import register as _register_carpark_api


def register_all(mcp) -> None:
    """Register every tool module on the given FastMCP server instance."""
    _register_api_tools(mcp)
    _register_carpark_api(mcp)
    # _register_your_next_api(mcp)   ← add future API modules here
