"""AgentCore MCP Server — streamable-HTTP transport, containerised for Bedrock AgentCore.

The MCP server exposes tools that front your API Gateway endpoints. The Strands agent
(aws/agentcore/agents/main_agent) connects to this server as an MCP client at startup
and discovers all registered tools automatically.

Architecture:
    Browser → API GW → Lambda → Strands Agent (AgentCore)
                                       ↕  MCP (HTTP)
                                 This MCP Server (AgentCore)
                                       ↕  REST
                                  Your API Gateway

Endpoints served by this container:
    GET  /health   — AgentCore liveness probe
    POST /mcp      — MCP streamable-HTTP endpoint (tool discovery + invocation)

Environment variables:
    API_BASE_URL   — Base URL of the REST API this MCP server fronts
                     e.g. https://abc123.execute-api.us-east-1.amazonaws.com/prod
    PORT           — HTTP port (default: 8080)

Connecting from the Strands agent:
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    mcp_url = os.getenv("MCP_SERVER_URL", "http://mcp-server:8080/mcp")
    async with MCPClient(lambda: streamablehttp_client(mcp_url)) as mcp_client:
        tools = mcp_client.list_tools_sync()
        agent = Agent(model=..., tools=tools)
"""

import logging
import os

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

# load_secrets() must run before tool modules are imported so that every
# os.getenv() call inside them sees the injected Secrets Manager values.
from config import load_secrets
load_secrets()

from tools import register_all

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="AgentCore API Gateway MCP Server",
    instructions=(
        "You are connected to a set of API tools. "
        "Use them to fulfil user requests. "
        "Always prefer the most specific tool available for a task."
    ),
)

# Register all tool modules (add new modules in tools/__init__.py)
register_all(mcp)

# ── ASGI app ──────────────────────────────────────────────────────────────────

# stateless_http=True: no session affinity — safe for AgentCore's horizontal scaling.
# The MCP protocol endpoint will be reachable at POST /mcp.
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "server": mcp.name})


# Pass mcp_app.lifespan so FastMCP can initialise its session manager.
app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Mount("/", app=mcp_app),
    ],
    lifespan=mcp_app.lifespan,
)

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    logger.info("Starting MCP server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
