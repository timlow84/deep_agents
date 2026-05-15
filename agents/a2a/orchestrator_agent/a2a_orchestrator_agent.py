"""A2A Orchestrator Agent server.

Runs an A2A-protocol server that accepts incoming requests and delegates to
specialist sub-agents (weather, etc.) via A2A.  Simultaneously runs a background
Kafka listener: when a message arrives on the configured topic the agent extracts
the "query" field, calls the weather A2A agent, and logs the result.

Run (weather A2A server must already be running):
    uv run python agents/a2a/orchestrator_agent/a2a_orchestrator_agent.py

Environment variables (.env):
    A2A_ORCHESTRATOR_AGENT_PORT         (default: 9000)
    A2A_ORCHESTRATOR_AGENT_KAFKA_URL    Kafka REST Proxy base URL (e.g. http://localhost)
    A2A_ORCHESTRATOR_AGENT_KAFKA_PORT   Kafka REST Proxy port    (e.g. 8082)
    A2A_ORCHESTRATOR_AGENT_KAFKA_TOPIC  Kafka topic to subscribe to
    A2A_WEATHER_AGENT_PORT              (default: 9001)
    WEATHER_AGENT_URL                   (overrides port-based default if set)
"""

# Standard library (safe to import before SSL patch — no httpx clients created)
import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from typing import Final

# ---------------------------------------------------------------------------
# Bootstrap: SSL patch + env vars must load before any third-party import
# that creates an httpx client (LangFuse, langchain-anthropic, etc.).
# Mirrors the startup order in app/main.py.
# ---------------------------------------------------------------------------
import truststore  # noqa: E402  # isort: skip

truststore.inject_into_ssl()

from dotenv import load_dotenv  # noqa: E402  # isort: skip

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

# Third-party (SSL-patched context is now active)
import httpx  # noqa: E402
import langfuse as _langfuse_module  # noqa: E402
import uvicorn  # noqa: E402
from a2a.client import ClientConfig, create_client  # noqa: E402
from a2a.helpers import (  # noqa: E402
    new_task_from_user_message,
    new_text_artifact,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import (  # noqa: E402
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    SendMessageRequest,
)
from a2a.types.a2a_pb2 import (  # noqa: E402
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langfuse import propagate_attributes  # noqa: E402
from langfuse.langchain import CallbackHandler  # noqa: E402
from langgraph.prebuilt import create_react_agent  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

# Local imports (require sys.path insert above)
from agents.llm_factory import get_llm  # noqa: E402
from tools.kafka.kafka_client import run_kafka_listener  # noqa: E402

# The agent name to be used in LangFuse
AGENT_NAME: Final[str] = "a2a_orchestrator_agent"

LOG_FORMAT = os.getenv("LOG_FORMAT", "%(levelname)s %(name)s: %(message)s")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT)
log = logging.getLogger(__name__)

_WEATHER_AGENT_URL = os.getenv(
    "WEATHER_AGENT_URL",
    f"http://localhost:{os.getenv('A2A_WEATHER_AGENT_PORT', '9001')}",
)

_KAFKA_URL = os.getenv("A2A_ORCHESTRATOR_AGENT_KAFKA_URL", "http://localhost")
_KAFKA_PORT = int(os.getenv("A2A_ORCHESTRATOR_AGENT_KAFKA_PORT", "8082"))
_KAFKA_TOPIC = os.getenv("A2A_ORCHESTRATOR_AGENT_KAFKA_TOPIC", "to_orchestrator_agent")
_KAFKA_GROUP_ID = os.getenv("A2A_ORCHESTRATOR_AGENT_KAFKA_GROUP_ID")
_KAFKA_SEEK_OFFSET: int | None = None
if _v := os.getenv("A2A_ORCHESTRATOR_AGENT_KAFKA_SEEK_OFFSET", "").strip():
    try:
        _KAFKA_SEEK_OFFSET = int(_v)
    except ValueError:
        pass  # env var set to a non-integer (e.g. placeholder comment) — treat as unset

_SYSTEM_PROMPT = (
    "You are an orchestrator agent. When the user asks about weather, "
    "delegate the entire question to the weather_agent tool exactly as phrased. "
    "Return the weather agent's answer verbatim without modification."
)

# ---------------------------------------------------------------------------
# Helpers (mirrors a2a_weather_agent.py for consistency)
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

        if getattr(part, "kind", None) == "text":
            candidate = getattr(part, "text", None)
            if isinstance(candidate, str):
                text = candidate

        if text is None and hasattr(part, "WhichOneof"):
            try:
                if part.WhichOneof("kind") == "text":
                    text = getattr(part.text, "text", None)
            except Exception:
                pass

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
# A2A client helper – calls the weather agent
# ---------------------------------------------------------------------------


async def _call_weather_a2a(query: str) -> str:
    """Send *query* to the weather A2A server and return the text response."""
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
# LangChain tool – wraps A2A weather call for use inside the ReAct graph
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
# Shared graph — used by both the Kafka handler and OrchestratorAgentExecutor
# so a single LLM+tool graph instance serves all entry points.
# ---------------------------------------------------------------------------

_graph = create_react_agent(model=get_llm(), tools=[weather_agent], prompt=_SYSTEM_PROMPT)

# ---------------------------------------------------------------------------
# Kafka message handler
#
# Pattern for reuse in future agents:
#   1. Debug-log the raw message.
#   2. Extract the user query from the "query" field.
#   3. Info-log and print the query.
#   4. Invoke the shared ReAct graph (with LangFuse tracing) instead of
#      calling _call_weather_a2a directly, so the LLM reasoning step is
#      captured in LangFuse just like the A2A execute() path.
#   5. Info-log and print the response.
# ---------------------------------------------------------------------------


async def on_kafka_message(message: dict) -> None:
    """Handle a message received from the Kafka topic."""
    log.debug("Kafka message received: %s", json.dumps(message, ensure_ascii=False))

    lf = _langfuse_module.get_client()
    session_id = str(uuid.uuid4())
    query = message.get("query")
    if not query:
        log.warning("Kafka message missing 'query' field — skipping: %s", message)
        with propagate_attributes(
            session_id=session_id, trace_name=AGENT_NAME, tags=["on_kafka_message()"]
        ):
            with lf.start_as_current_observation(
                name="missing_query_field",
                as_type="span",
                level="WARNING",
                input=json.dumps(message, ensure_ascii=False),
                metadata={"reason": "missing_query_field"},
            ):
                pass
        lf.flush()
        return

    print(f"[Kafka] Query received: {query}")
    log.info("Kafka query received: %s", query)

    langfuse_cb = CallbackHandler()
    log.info("Calling orchestrator graph for Kafka query: %s", query)
    try:
        # with propagate_attributes(session_id=session_id, trace_name=query[:120]):
        # [Reference] -> https://langfuse.com/docs/observability/features/tags
        with propagate_attributes(
            session_id=session_id, trace_name=AGENT_NAME, tags=["on_kafka_message()", query[:120]]
        ):
            # Invoke the shared ReAct graph instead of calling _call_weather_a2a directly, so
            # the LLM reasoning step is captured in LangFuse just like the A2A execute() path.
            result = await _graph.ainvoke(
                {"messages": [HumanMessage(content=query)]},
                config={"callbacks": [langfuse_cb]},
            )
        response = _normalise_content(result["messages"][-1].content)
    except Exception:
        log.exception("Failed to get response for Kafka query: %s", query)
        with propagate_attributes(
            session_id=session_id, trace_name=AGENT_NAME, tags=["on_kafka_message()", query[:120]]
        ):
            with lf.start_as_current_observation(
                name="graph_invocation_error",
                as_type="span",
                level="ERROR",
                input=query,
            ):
                pass
        return
    finally:
        lf.flush()

    print(f"[Kafka] Weather agent response:\n{response}")
    log.info("Weather agent response for Kafka query: %s", response)


# ---------------------------------------------------------------------------
# A2A AgentExecutor
# ---------------------------------------------------------------------------


class OrchestratorAgentExecutor(AgentExecutor):
    """Routes incoming A2A requests to the appropriate specialist sub-agent."""

    def __init__(self) -> None:
        self._graph = _graph

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
                    message=new_text_message("Routing your request…"),
                ),
            )
        )

        user_text = _extract_text(context.message)
        log.info("A2A orchestrator request: %s", user_text)

        langfuse_cb = CallbackHandler()
        session_id = context.task_id or str(uuid.uuid4())
        log.debug("Starting ainvoke: session_id=%s trace_name=%r", session_id, user_text[:60])
        try:
            # with propagate_attributes(session_id=session_id, trace_name=user_text[:120]):
            # [Reference] -> https://langfuse.com/docs/observability/features/tags
            with propagate_attributes(
                session_id=session_id, trace_name=AGENT_NAME, tags=["execute()", user_text[:120]]
            ):
                # Invoke the shared ReAct graph with LangFuse tracing, so the LLM reasoning step is
                # captured in LangFuse. Also see the method used for Kafka messages
                # on_kafka_message()above, which follows the same pattern.
                result = await self._graph.ainvoke(
                    {"messages": [HumanMessage(content=user_text)]},
                    config={"callbacks": [langfuse_cb]},
                )
            answer = _normalise_content(result["messages"][-1].content)
            log.debug("ainvoke completed, answer length=%d", len(answer))
        except Exception as exc:
            log.exception("Orchestrator agent error")
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
                artifact=new_text_artifact(name="orchestrator_result", text=answer),
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


def build_app(host: str = "0.0.0.0", port: int = 9000) -> Starlette:
    card_host = "localhost" if host == "0.0.0.0" else host

    routing_skill = AgentSkill(
        id="query_routing",
        name="Query Routing",
        description=(
            "Routes user queries to the most appropriate specialist sub-agent. "
            "Currently supports weather queries via the weather A2A agent."
        ),
        tags=["orchestrator", "routing", "weather"],
        examples=[
            "What is the weather in Singapore?",
            "Will it rain in London this week?",
            "Give me the 5-day forecast for Tokyo.",
        ],
    )

    agent_card = AgentCard(
        name="Orchestrator Agent",
        description=(
            "An orchestrator that routes user queries to specialist sub-agents "
            "via the A2A protocol. Also accepts queries autonomously from a Kafka topic."
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
        skills=[routing_skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=OrchestratorAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = []
    routes.extend(create_agent_card_routes(agent_card))
    routes.extend(create_jsonrpc_routes(request_handler, "/"))

    _kafka_task: asyncio.Task | None = None

    async def _start_kafka_listener() -> None:
        nonlocal _kafka_task
        _kafka_task = asyncio.create_task(
            run_kafka_listener(
                server_url=_KAFKA_URL,
                port=_KAFKA_PORT,
                topic=_KAFKA_TOPIC,
                on_message=on_kafka_message,
                group_id=_KAFKA_GROUP_ID,
                seek_offset=_KAFKA_SEEK_OFFSET,
            ),
            name="kafka-listener",
        )
        log.info(
            "Kafka listener task scheduled: %s:%d topic=%s, seek_offset=%s",
            _KAFKA_URL,
            _KAFKA_PORT,
            _KAFKA_TOPIC,
            _KAFKA_SEEK_OFFSET,
        )

    async def _stop_kafka_listener() -> None:
        if _kafka_task and not _kafka_task.done():
            _kafka_task.cancel()
            try:
                await _kafka_task
            except asyncio.CancelledError:
                pass
        log.info("Kafka listener stopped")

    @contextlib.asynccontextmanager
    async def _lifespan(app):
        await _start_kafka_listener()
        yield
        await _stop_kafka_listener()

    return Starlette(
        routes=routes,
        lifespan=_lifespan,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _port = int(os.getenv("A2A_ORCHESTRATOR_AGENT_PORT", "9000"))
    _host = os.getenv("A2A_ORCHESTRATOR_HOST", "0.0.0.0")
    log.info("Starting A2A Orchestrator Agent on %s:%d", _host, _port)
    uvicorn.run(build_app(_host, _port), host=_host, port=_port)
