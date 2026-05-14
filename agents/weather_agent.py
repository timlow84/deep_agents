"""Weather sub-agent specification for use with create_deep_agent."""

import os
import sys
from typing import Final

from deepagents import SubAgent

from agents.llm_factory import get_llm            # selects provider via LLM_PROVIDER_SELECTOR env var
from agents.llm_factory import get_anthropic_llm  # explicit Anthropic
# from agents.llm_factory import get_openai_llm   # switch to local Ollama via Portkey
# from agents.llm_factory import get_nvidia_llm   # switch to NVIDIA NIM (build.nvidia.com)

AGENT_NAME: Final[str] = "weather_agent"

WEATHER_AGENT_PROMPT = (
    "You are a weather agent with three tools: geocode_city, get_current_weather, and get_5day_forecast. "
    "You MUST always use tools — never answer from memory. "
    "Step 1: call geocode_city with the city name to get latitude and longitude. "
    "Step 2a: if the user asks about current weather, call get_current_weather with those coordinates "
    "and report the temperature, conditions, humidity, wind speed, and feels-like temperature. "
    "Step 2b: if the user asks about a forecast or future weather (e.g. 'next 5 days', 'this week', "
    "'will it rain'), call get_5day_forecast with those coordinates and summarise each day's "
    "high/low temperature and conditions."
)

_MCP_SERVER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tools", "weather_mcp_server.py")
)


def get_weather_mcp_config() -> dict:
    """MCP client config for the OpenWeatherMap server (stdio transport).

    Call this after load_dotenv() so that os.environ already contains the API keys.
    The env snapshot is passed explicitly to the subprocess so it receives all vars
    regardless of how the subprocess is spawned by langchain-mcp-adapters.
    """
    return {
        "weather": {
            "command": sys.executable,
            "args": [_MCP_SERVER],
            "transport": "stdio",
            "env": dict(os.environ),
        }
    }


def build_weather_subagent(tools: list, model_name: str | None = None) -> SubAgent:
    """Build a SubAgent spec for the weather agent."""
    return {
        "name": "weather_agent",
        "description": (
            "Fetches current weather and 5-day forecasts for any city worldwide using OpenWeatherMap. "
            "Use this agent when the user asks about current weather, temperature, humidity, wind speed, "
            "conditions, or future weather (forecast, next N days, will it rain, this week, etc.)."
        ),
        "system_prompt": WEATHER_AGENT_PROMPT,
        "tools": tools,
        "model": get_llm(model_name),  # provider selected by LLM_PROVIDER_SELECTOR env var
    }
