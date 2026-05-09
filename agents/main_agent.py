"""Orchestrator main agent — uses deepagents (Anthropic) or plain ReAct (NVIDIA/Portkey)."""

import os

from deepagents import SubAgent, create_deep_agent
from langgraph.prebuilt import create_react_agent

from agents.llm_factory import get_anthropic_llm  # switch to Anthropic Claude - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_openai_llm     # switch to local Ollama via Portkey - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_nvidia_llm     # switch to NVIDIA NIM (build.nvidia.com) - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_llm            # selects provider via LLM_PROVIDER_SELECTOR env var

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are an orchestrator agent. When the user asks about the weather in any city or location, "
    "delegate the task to the weather_agent sub-agent to fetch live data, "
    "then present the results in a clear, friendly summary."
)

REACT_SYSTEM_PROMPT = (
    "You are a helpful weather assistant with exactly two tools: geocode_city and get_current_weather.\n"
    "Follow these steps in order and stop after step 3:\n"
    "1. Call geocode_city with the city name to get latitude and longitude.\n"
    "2. Call get_current_weather with those coordinates.\n"
    "3. After receiving the weather data, write a friendly summary to the user and stop — "
    "do NOT call any further tools.\n"
    "Never answer from memory. Always use the tools for live data."
)


def build_main_agent(weather_subagent: SubAgent, tools: list | None = None, model_name: str | None = None):
    """Build the orchestrator graph.

    Uses deepagents when LLM_PROVIDER_SELECTOR=ANTHROPIC (Claude-specific prompts required).
    Falls back to a plain LangGraph ReAct agent for NVIDIA and PORTKEY providers so that
    no Anthropic API key is needed.
    """
    provider = os.getenv("LLM_PROVIDER_SELECTOR", "ANTHROPIC").upper()

    if provider != "ANTHROPIC":
        # deepagents requires Claude — use a standard ReAct agent for other providers
        return create_react_agent(
            model=get_llm(model_name),
            tools=tools or [],
            prompt=REACT_SYSTEM_PROMPT,
        )

    return create_deep_agent(
        model=get_llm(model_name),
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        subagents=[weather_subagent],
    )
