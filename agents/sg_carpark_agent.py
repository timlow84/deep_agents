"""SG Carpark sub-agent specification for use with create_deep_agent."""

import os
import sys
from typing import Final

from deepagents import SubAgent

from agents.llm_factory import get_llm            # selects provider via LLM_PROVIDER_SELECTOR env var
from agents.llm_factory import get_anthropic_llm  # explicit Anthropic
# from agents.llm_factory import get_openai_llm   # switch to local Ollama via Portkey
# from agents.llm_factory import get_nvidia_llm   # switch to NVIDIA NIM (build.nvidia.com)

AGENT_NAME: Final[str] = "sg_carpark_agent"

CARPARK_AGENT_PROMPT = (
    "You are a Singapore carpark agent with one tool: get_nearby_carparks. "
    "You MUST always use tools — never answer from memory. "
    "When given a latitude and longitude, call get_nearby_carparks to find real-time carpark availability nearby. "
    "Step 1: Call get_nearby_carparks with the provided lat, lon (and optionally radius_km and limit). "
    "Step 2: Report each carpark's name (development), area, available lots, "
    "lot type (C=car, Y=motorcycle, H=heavy vehicle), agency, and distance in km. "
    "Sort results by distance. If no carparks are found within the radius, say so clearly "
    "and suggest the user try a larger radius_km."
)

_MCP_SERVER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tools", "mcp", "sg_carpark_mcp_server.py")
)


def get_sg_carpark_mcp_config() -> dict:
    """MCP client config for the SG Carpark server (stdio transport).

    Call this after load_dotenv() so that os.environ already contains the API keys.
    The env snapshot is passed explicitly to the subprocess so it receives all vars
    regardless of how the subprocess is spawned by langchain-mcp-adapters.
    """
    return {
        "sg_carparks": {
            "command": sys.executable,
            "args": [_MCP_SERVER],
            "transport": "stdio",
            "env": dict(os.environ),
        }
    }


def build_sg_carpark_subagent(tools: list, model_name: str | None = None) -> SubAgent:
    """Build a SubAgent spec for the SG carpark agent."""
    return {
        "name": "sg_carpark_agent",
        "description": (
            "Finds the nearest Singapore carparks with real-time lot availability using LTA DataMall. "
            "Use this agent when the user asks about nearby carparks, parking availability, or where to park "
            "in Singapore. Requires a latitude and longitude — use the user's current location coordinates "
            "from the [User's current location: lat=X, lon=Y] context prefix if present."
        ),
        "system_prompt": CARPARK_AGENT_PROMPT,
        "tools": tools,
        "model": get_llm(model_name),
    }
