"""A2A-protocol weather agent server.

Exposes geocoding, current weather, and 5-day forecast capabilities as an
A2A-compliant JSON-RPC service.  Any A2A-compatible client (or another agent)
can discover and call this server.

Run:
    uv run python agents/a2a/weather_agent/a2a_weather_agent.py

Environment variables (.env):
    A2A_WEATHER_HOST        (default: 0.0.0.0)
    A2A_WEATHER_AGENT_PORT  (default: 9001)
    OPENWEATHERMAP_API_KEY  (required)

Endpoints served by Starlette/uvicorn:
    GET  /.well-known/agent-card.json   – agent card discovery
    POST /                         – A2A JSON-RPC (message/send, message/stream, tasks/get)
"""

import json
import logging
import os
import sys
import uuid
from typing import Final

import langfuse as _langfuse_module
import uvicorn
from a2a.helpers import new_task_from_user_message, new_text_artifact, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.types.a2a_pb2 import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from dotenv import load_dotenv
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from langgraph.prebuilt import create_react_agent
from starlette.applications import Starlette

# ---------------------------------------------------------------------------
# Bootstrap – must happen before any ChatAnthropic instantiation
# ---------------------------------------------------------------------------
load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from agents.llm_factory import get_llm  # noqa: E402 – path insert must come first
from tools.mcp.weather_mcp_server import (  # noqa: E402
    _geocode_city_impl,
    _get_current_weather_impl,
    _get_forecast_impl,
)

AGENT_NAME: Final[str] = "a2a_weather_agent"

LOG_FORMAT = os.getenv("LOG_FORMAT", "%(levelname)s %(name)s: %(message)s")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LangChain tools – thin wrappers around the weather_mcp_server helpers
# (direct HTTP calls; no MCP subprocess needed for a standalone A2A server)
# ---------------------------------------------------------------------------


@tool
async def geocode_city(city: str) -> str:
    """Convert a city name to geographical coordinates (up to 5 matches with lat/lon)."""
    return json.dumps(await _geocode_city_impl(city))


@tool
async def get_current_weather(lat: float, lon: float, units: str = "metric") -> str:
    """Get current weather for a latitude/longitude.
    units: 'metric' (°C), 'imperial' (°F), or 'standard' (K).
    """
    return json.dumps(await _get_current_weather_impl(lat, lon, units))


@tool
async def get_5day_forecast(lat: float, lon: float, units: str = "metric") -> str:
    """Get a 5-day daily forecast for a latitude/longitude.
    units: 'metric' (°C), 'imperial' (°F), or 'standard' (K).
    """
    return json.dumps(await _get_forecast_impl(lat, lon, units))


_WEATHER_TOOLS = [geocode_city, get_current_weather, get_5day_forecast]

_SYSTEM_PROMPT = (
    "You are a weather agent with three tools: geocode_city, get_current_weather, and get_5day_forecast. "
    "You MUST always use tools — never answer from memory. "
    "Step 1: call geocode_city with the city name to get latitude and longitude. "
    "Step 2a: if the user asks about current weather, call get_current_weather with those coordinates "
    "and report the temperature, conditions, humidity, wind speed, and feels-like temperature. "
    "Step 2b: if the user asks about a forecast or future weather (e.g. 'next 5 days', 'this week', "
    "'will it rain'), call get_5day_forecast with those coordinates and summarise each day's "
    "high/low temperature and conditions."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(message) -> str:
    """Extract plain text from an A2A Message.

    Handles both Pydantic (kind='text', text: str) and proto oneof layouts
    (WhichOneof('kind') == 'text', part.text.text).
    """
    parts = getattr(message, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        text: str | None = None

        # Pydantic / dict-style: part.kind == "text" and part.text is a plain string
        if getattr(part, "kind", None) == "text":
            candidate = getattr(part, "text", None)
            if isinstance(candidate, str):
                text = candidate

        # Proto oneof: part.WhichOneof('kind') == 'text' → part.text.text
        if text is None and hasattr(part, "WhichOneof"):
            try:
                if part.WhichOneof("kind") == "text":
                    text = getattr(part.text, "text", None)
            except Exception:
                pass

        # Fallback: direct string attribute named 'text'
        if text is None:
            candidate = getattr(part, "text", None)
            if isinstance(candidate, str):
                text = candidate

        if text:
            texts.append(text)

    return " ".join(texts)


def _normalise_content(content) -> str:
    """Normalise LangChain message content (str or list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(block.get("text", "") for block in content if isinstance(block, dict))
    return str(content)


# ---------------------------------------------------------------------------
# A2A AgentExecutor
# ---------------------------------------------------------------------------


class WeatherAgentExecutor(AgentExecutor):
    """Runs a LangGraph ReAct weather agent and maps its output to A2A events."""

    def __init__(self) -> None:
        self._graph = create_react_agent(
            model=get_llm(),
            tools=_WEATHER_TOOLS,
            prompt=_SYSTEM_PROMPT,
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_text_message("Fetching weather data…"),
                ),
            )
        )

        user_text = _extract_text(context.message)
        log.info("A2A weather request: %s", user_text)

        langfuse_cb = CallbackHandler()
        session_id = context.task_id or str(uuid.uuid4())
        try:
            # with propagate_attributes(session_id=session_id, trace_name=user_text[:120]):
            #     result = await self._graph.ainvoke(
            #         {"messages": [("user", user_text)]},
            #         config={"callbacks": [langfuse_cb]},
            #     )
            # [Reference] -> https://langfuse.com/docs/observability/features/tags
            with propagate_attributes(
                session_id=session_id, trace_name=AGENT_NAME, tags=["execute()", user_text[:120]]
            ):
                result = await self._graph.ainvoke(
                    {"messages": [("user", user_text)]},
                    config={"callbacks": [langfuse_cb]},
                )
            answer = _normalise_content(result["messages"][-1].content)
        except Exception as exc:
            log.exception("Weather agent error")
            answer = f"Error: {exc}"
            with propagate_attributes(
                session_id=session_id, trace_name=AGENT_NAME, tags=["execute()", user_text[:120]]
            ):
                with _langfuse_module.get_client().start_as_current_observation(
                    name="graph_invocation_error",
                    as_type="span",
                    level="ERROR",
                    input=user_text,
                ):
                    pass
        finally:
            _langfuse_module.get_client().flush()

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                artifact=new_text_artifact(name="weather_result", text=answer),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported")


# ---------------------------------------------------------------------------
# Starlette app factory
# ---------------------------------------------------------------------------


def build_app(host: str = "0.0.0.0", port: int = 9001) -> Starlette:
    # Advertise localhost when listening on all interfaces — 0.0.0.0 is not
    # a routable destination address and will cause clients to fail to connect.
    card_host = "localhost" if host == "0.0.0.0" else host

    current_weather_skill = AgentSkill(
        id="current_weather",
        name="Current Weather",
        description=(
            "Get real-time temperature, humidity, wind speed, and conditions "
            "for any city worldwide."
        ),
        tags=["weather", "current", "temperature", "humidity", "wind"],
        examples=[
            "What is the weather in Singapore?",
            "How hot is it in Tokyo right now?",
            "What are the current conditions in New York?",
        ],
    )

    forecast_skill = AgentSkill(
        id="weather_forecast",
        name="5-Day Weather Forecast",
        description=(
            "Get a 5-day daily forecast (high/low temperatures and conditions) "
            "for any city worldwide."
        ),
        tags=["weather", "forecast", "5-day", "future", "rain"],
        examples=[
            "What is the weather forecast for London this week?",
            "Will it rain in Paris over the next 5 days?",
            "What will the weather be like in Sydney next week?",
        ],
    )

    agent_card = AgentCard(
        name="Weather Agent",
        description=(
            "Provides real-time weather and 5-day forecasts for any city worldwide "
            "using the OpenWeatherMap API."
        ),
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=f"http://{card_host}:{port}",
            )
        ],
        skills=[current_weather_skill, forecast_skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = []
    routes.extend(create_agent_card_routes(agent_card))
    routes.extend(create_jsonrpc_routes(request_handler, "/"))

    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _port = int(os.getenv("A2A_WEATHER_AGENT_PORT", "9001"))
    _host = os.getenv("A2A_WEATHER_HOST", "0.0.0.0")
    log.info("Starting A2A Weather Agent on %s:%d", _host, _port)
    uvicorn.run(build_app(_host, _port), host=_host, port=_port)
