"""A2A Orchestrator Agent.

A LangGraph ReAct agent that delegates weather queries to the weather A2A
server via the A2A protocol.  The weather agent is exposed as a LangChain tool
so the orchestrator can route freely without any hard-coded weather logic.

Run (weather A2A server must be running first):
    uv run python agents/a2a/orchestrator_agent/a2a_orchestrator_client.py

Environment variables:
    A2A_WEATHER_AGENT_PORT  (default: 9001)  — matches the weather agent .env setting
    WEATHER_AGENT_URL       (overrides the port-based default if set)
"""

import asyncio
import logging
import os
import sys
import uuid
from typing import Final

import httpx
import langfuse as _langfuse_module
import truststore
from a2a.client import ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from langgraph.prebuilt import create_react_agent

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
truststore.inject_into_ssl()
load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from agents.llm_factory import get_llm  # noqa: E402 – path insert must come first

# The agent name to be used in LangFuse
AGENT_NAME: Final[str] = "a2a_orchestrator_client"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

_WEATHER_AGENT_URL = os.getenv(
    "WEATHER_AGENT_URL",
    f"http://localhost:{os.getenv('A2A_WEATHER_AGENT_PORT', '9001')}",
)

_SYSTEM_PROMPT = (
    "You are an orchestrator agent. When the user asks about weather, "
    "delegate the entire question to the weather_agent tool exactly as phrased. "
    "Return the weather agent's answer verbatim without modification."
)

# ---------------------------------------------------------------------------
# A2A client helper
# ---------------------------------------------------------------------------


async def _call_weather_a2a(query: str) -> str:
    """Send *query* to the weather A2A server and return the response text."""
    # LLM + multiple tool calls can take 30-60 s; raise the timeout well above
    # the default 5 s so the SSE stream doesn't cut off mid-response.
    http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    client = await create_client(
        agent=_WEATHER_AGENT_URL,
        client_config=ClientConfig(streaming=True, httpx_client=http),
    )
    try:
        request = SendMessageRequest(
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=[Part(text=query)],
            )
        )

        texts: list[str] = []
        async for event in client.send_message(request):
            which = event.WhichOneof("payload")
            if which == "artifact_update":
                artifact = event.artifact_update.artifact
                for part in artifact.parts:
                    if part.text:
                        texts.append(part.text)
            elif which == "message":
                for part in event.message.parts:
                    if part.text:
                        texts.append(part.text)

        return " ".join(texts) or "(no response from weather agent)"
    finally:
        await client.close()
        await http.aclose()


# ---------------------------------------------------------------------------
# LangChain tool wrapping the A2A call
# ---------------------------------------------------------------------------


@tool
async def weather_agent(query: str) -> str:
    """Ask the weather agent for current conditions or a 5-day forecast for any city.

    Examples:
      - "What is the weather in Singapore?"
      - "Will it rain in London this week?"
      - "Give me the 5-day forecast for Tokyo."
    """
    log.info("Delegating to weather A2A agent: %s", query)
    return await _call_weather_a2a(query)


# ---------------------------------------------------------------------------
# Orchestrator graph
# ---------------------------------------------------------------------------


def build_orchestrator():
    """Return a compiled LangGraph ReAct graph with the weather A2A tool."""
    return create_react_agent(
        model=get_llm(),
        tools=[weather_agent],
        prompt=_SYSTEM_PROMPT,
    )


# ---------------------------------------------------------------------------
# Interactive entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    graph = build_orchestrator()
    print(f"Orchestrator ready — weather agent at {_WEATHER_AGENT_URL}")
    print("Type a question or 'quit' to exit.\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            break

        # [Reference] -> https://langfuse.com/integrations/frameworks/langchain
        langfuse_cb = CallbackHandler()
        # Passing function name to LangFuse trace
        with propagate_attributes(tags=["main"]):
            try:
                # [Reference] -> https://langfuse.com/docs/observability/features/metadata
                with propagate_attributes(session_id=str(uuid.uuid4()), trace_name=AGENT_NAME):
                    result = await graph.ainvoke(
                        {"messages": [HumanMessage(content=query)]},
                        config={"callbacks": [langfuse_cb]},
                    )
            finally:
                _langfuse_module.get_client().flush()

        answer = result["messages"][-1].content
        if isinstance(answer, list):
            answer = " ".join(block.get("text", "") for block in answer if isinstance(block, dict))
        print(f"Agent: {answer}\n")


if __name__ == "__main__":
    asyncio.run(main())
