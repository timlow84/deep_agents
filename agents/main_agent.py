"""Orchestrator main agent — uses deepagents with any tool-calling LLM provider."""

import os

from deepagents import SubAgent, create_deep_agent

from agents.llm_factory import get_anthropic_llm  # switch to Anthropic Claude - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_openai_llm     # switch to local Ollama via Portkey - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_nvidia_llm     # switch to NVIDIA NIM (build.nvidia.com) - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_llm            # selects provider via LLM_PROVIDER_SELECTOR env var

_MAX_CARPARKS = int(os.getenv("MAX_CARPARKS", "20"))

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are an orchestrator agent. "
    "When the user asks about the weather in any city or location, "
    "delegate the task to the weather_agent sub-agent to fetch live data, "
    "then present the results in a clear, friendly summary. "
    "When the user asks about nearby carparks, parking availability, or where to park in Singapore, "
    "call get_nearby_carparks DIRECTLY — do NOT use the task tool for carpark queries. "
    "Extract the latitude and longitude from the [User's current location: lat=X, lon=Y] prefix "
    "if present; otherwise ask the user for their location. "
    f"If the user specifies a number of carparks (e.g. '5 nearest', 'top 3'), pass that number as the limit parameter, "
    f"but never exceed the maximum of {_MAX_CARPARKS} results. "
    "Present carpark results in a clear, friendly summary."
)


def build_main_agent(
    weather_subagent: SubAgent,
    carpark_tools: list | None = None,
    carpark_subagent: SubAgent | None = None,  # kept for API compatibility, not used
    tools: list | None = None,
    model_name: str | None = None,
):
    """Build the orchestrator graph using deepagents.

    Works with any tool-calling LLM (Anthropic, NVIDIA, PORTKEY, Ollama).
    AnthropicPromptCachingMiddleware silently no-ops for non-Anthropic models.

    Carpark tools are passed directly to create_deep_agent (not as a sub-agent) so that
    their on_tool_end events surface in the top-level astream_events stream and the
    frontend can intercept the raw JSON to render a table.
    """
    return create_deep_agent(
        model=get_llm(model_name),
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        subagents=[weather_subagent],
        tools=carpark_tools or [],  # direct tool — on_tool_end bubbles up to top level
    )
