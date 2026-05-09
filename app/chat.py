"""Chat endpoint with SSE streaming — task delegation status + final response."""

import json
import logging
import uuid

log = logging.getLogger(__name__)

import langfuse as _langfuse_module
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel

router = APIRouter()

_TOOL_LABELS = {
    "task": "Delegating to weather agent...",
    "geocode_city": "Geocoding city...",
    "get_current_weather": "Fetching weather data...",
}


def _langfuse_handler() -> CallbackHandler:
    """Create a per-request LangFuse callback handler.

    LangFuse v4 reads LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY / LANGFUSE_HOST
    from environment variables; the constructor no longer accepts them directly.
    """
    return CallbackHandler()


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    graph = request.app.state.graph
    messages = []
    for msg in req.history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        else:
            messages.append(AIMessage(content=msg.content))
    messages.append(HumanMessage(content=req.message))
    langfuse = _langfuse_handler()
    session_id = str(uuid.uuid4())

    async def event_stream():
        try:
            with propagate_attributes(
                session_id=session_id, trace_name=req.message[:120]
            ):
                async for event in graph.astream_events(
                    {"messages": messages},
                    config={"callbacks": [langfuse]},
                    version="v2",
                ):
                    etype = event["event"]
                    log.debug("[event] %s name=%r", etype, event.get("name"))

                    # Stream status messages for known tool calls
                    if etype == "on_tool_start":
                        log.debug("[tool_start] name=%r input=%s", event["name"], event["data"].get("input"))
                        label = _TOOL_LABELS.get(event["name"])
                        if label:
                            yield f"data: {json.dumps({'type': 'status', 'text': label})}\n\n"

                    # Log all chain_end events to discover the correct graph name
                    elif etype == "on_chain_end":
                        log.debug("[chain_end] name=%r keys=%s", event["name"], list(event["data"].get("output", {}).keys()) if isinstance(event["data"].get("output"), dict) else type(event["data"].get("output")).__name__)

                    # Emit final response when the top-level graph finishes
                    if etype == "on_chain_end" and event["name"] == "LangGraph":
                        output = event["data"].get("output", {})
                        msgs = output.get("messages", [])
                        log.debug("[chain_end LangGraph] %d messages, types=%s", len(msgs), [getattr(m, "type", "?") for m in msgs])
                        for m in msgs:
                            log.debug("[msg] type=%r content=%r tool_calls=%r", getattr(m, "type", "?"), getattr(m, "content", "")[:80], getattr(m, "tool_calls", None))
                        for m in reversed(msgs):
                            raw = getattr(m, "content", "")
                            if raw and getattr(m, "type", "") == "ai":
                                # content may be a string or a list of content blocks
                                if isinstance(raw, list):
                                    text = " ".join(
                                        (
                                            b.get("text", "")
                                            if isinstance(b, dict)
                                            else str(b)
                                        )
                                        for b in raw
                                    )
                                else:
                                    text = str(raw)
                                yield f"data: {json.dumps({'type': 'response', 'text': text})}\n\n"
                                break

        except Exception as exc:
            log.exception("[exception] %s: %s", type(exc).__name__, exc)
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
        finally:
            _langfuse_module.get_client().flush()

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
