"""Orchestrator main agent built with deepagents.create_deep_agent."""

from deepagents import SubAgent, create_deep_agent

from agents.llm_factory import get_anthropic_llm  # switch to Anthropic Claude - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_openai_llm     # switch to local Ollama via Portkey - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_nvidia_llm     # switch to NVIDIA NIM (build.nvidia.com) - DO NOT CHANGE THIS LINE
from agents.llm_factory import get_llm            # selects provider via LLM_PROVIDER_SELECTOR env var

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are an orchestrator agent. When the user asks about the weather in any city or location, "
    "delegate the task to the weather_agent sub-agent to fetch live data, "
    "then present the results in a clear, friendly summary."
)


def build_main_agent(weather_subagent: SubAgent, model_name: str | None = None):
    """Build the deep-agent orchestrator with the weather sub-agent registered.

    The orchestrator has no direct tools. It delegates weather queries to
    weather_agent via the built-in `task` tool provided by deepagents.
    """
    return create_deep_agent(
        model=get_llm(model_name, deepagents=True),  # deepagents requires Anthropic Claude
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        subagents=[weather_subagent],
    )
