"""Chat endpoint with SSE streaming — task delegation status + final response."""

import json
import logging
import os
import uuid
from typing import Final

AGENT_NAME: Final[str] = "main_agent"

log = logging.getLogger(__name__)

MAX_CARPARKS = int(os.getenv("MAX_CARPARKS", "20"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "25"))

import langfuse as _langfuse_module
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel

router = APIRouter()


class CarparksRequest(BaseModel):
    location: Location
    limit: int = 5
    radius_km: float = 2.0


class WeatherRequest(BaseModel):
    city: str


class WeatherCoordsRequest(BaseModel):
    lat: float
    lon: float


@router.get("/config")
async def get_config():
    """Expose runtime configuration to the frontend."""
    return {"max_carparks": MAX_CARPARKS}


@router.post("/carparks")
async def carparks_direct(req: CarparksRequest):
    """Direct carpark lookup — bypasses the LLM for /carparks N slash commands."""
    from tools.mcp.sg_carpark_mcp_server import _find_nearby_carparks  # noqa: PLC0415

    capped_limit = min(req.limit, MAX_CARPARKS)
    results = await _find_nearby_carparks(
        req.location.lat, req.location.lon, req.radius_km, capped_limit
    )
    return {"carparks": results, "requested": capped_limit}


@router.post("/weather")
async def weather_direct(req: WeatherRequest):
    """Direct weather lookup — bypasses the LLM for /weather [location] slash commands."""
    from tools.mcp.weather_mcp_server import (  # noqa: PLC0415
        _geocode_city_impl,
        _get_current_weather_impl,
        _get_forecast_impl,
    )

    geo = await _geocode_city_impl(req.city)
    if not geo:
        return {"error": f"Could not find location: {req.city}"}
    match = geo[0]
    weather = await _get_current_weather_impl(match["lat"], match["lon"], units="metric")
    forecast = await _get_forecast_impl(match["lat"], match["lon"], units="metric")
    return {
        "city": match.get("name", req.city),
        "country": match.get("country", ""),
        "lat": match["lat"],
        "lon": match["lon"],
        "temp": weather["main"]["temp"],
        "feels_like": weather["main"]["feels_like"],
        "humidity": weather["main"]["humidity"],
        "wind_speed": weather["wind"]["speed"],
        "description": weather["weather"][0]["description"],
        "icon": weather["weather"][0]["icon"],
        "forecast": forecast,
    }


@router.post("/weather/by-coords")
async def weather_by_coords(req: WeatherCoordsRequest):
    """Direct weather lookup by lat/lon — used when /weather is typed with no city."""
    from tools.mcp.weather_mcp_server import _get_current_weather_impl, _get_forecast_impl  # noqa: PLC0415

    weather = await _get_current_weather_impl(req.lat, req.lon, units="metric")
    forecast = await _get_forecast_impl(req.lat, req.lon, units="metric")
    return {
        "city": weather.get("name", "Current Location"),
        "country": weather.get("sys", {}).get("country", ""),
        "lat": req.lat,
        "lon": req.lon,
        "temp": weather["main"]["temp"],
        "feels_like": weather["main"]["feels_like"],
        "humidity": weather["main"]["humidity"],
        "wind_speed": weather["wind"]["speed"],
        "description": weather["weather"][0]["description"],
        "icon": weather["weather"][0]["icon"],
        "forecast": forecast,
    }


_TOOL_LABELS = {
    "task": "Delegating to sub-agent...",
    "geocode_city": "Geocoding city...",
    "get_current_weather": "Fetching weather data...",
    "get_nearby_carparks": "Finding nearby carparks...",
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


class Location(BaseModel):
    lat: float
    lon: float


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    location: Location | None = None


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    graph = request.app.state.graph
    messages = []
    for msg in req.history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        else:
            messages.append(AIMessage(content=msg.content))
    user_text = req.message
    if req.location:
        user_text = (
            f"[User's current location: lat={req.location.lat:.5f}, lon={req.location.lon:.5f}] "
            + user_text
        )
    messages.append(HumanMessage(content=user_text))
    langfuse = _langfuse_handler()
    session_id = str(uuid.uuid4())

    async def event_stream():
        try:
            # [Doc Reference] https://langfuse.com/docs/observability/features/metadata
            with propagate_attributes(session_id=session_id, trace_name=req.message[:120]):
                async for event in graph.astream_events(
                    {"messages": messages},
                    # Higher limit needed for multi-step chains: each tool call + model response
                    # costs ~2 steps, and non-Anthropic models (NVIDIA, Ollama) often require
                    # more intermediate steps before emitting a final tool call or answer.
                    config={"callbacks": [langfuse], "recursion_limit": RECURSION_LIMIT},
                    version="v2",
                ):
                    etype = event["event"]
                    log.debug("[event] %s name=%r", etype, event.get("name"))

                    # Stream status messages for known tool calls
                    if etype == "on_tool_start":
                        log.debug(
                            "[tool_start] name=%r input=%s",
                            event["name"],
                            event["data"].get("input"),
                        )
                        label = _TOOL_LABELS.get(event["name"])
                        if label:
                            yield f"data: {json.dumps({'type': 'status', 'text': label})}\n\n"

                    # Emit structured carpark data so the frontend can render a table
                    elif etype == "on_tool_end":
                        log.debug(
                            "[tool_end] name=%r output_type=%s preview=%r",
                            event["name"],
                            type(event["data"].get("output")).__name__,
                            str(event["data"].get("output", ""))[:500],
                        )
                        if event["name"] == "get_nearby_carparks":
                            raw = event["data"].get("output", "")
                            # langchain-mcp-adapters uses response_format="content_and_artifact":
                            # on_tool_end output may be a ToolMessage, a string, or a list of
                            # content blocks like [{"type": "text", "text": "[{...}]"}]
                            if hasattr(raw, "content"):
                                raw = raw.content
                            # Unwrap MCP content-block list: [{"type":"text","text":"..."}]
                            if (
                                isinstance(raw, list)
                                and raw
                                and isinstance(raw[0], dict)
                                and "type" in raw[0]
                            ):
                                text_parts = [
                                    b.get("text", "")
                                    for b in raw
                                    if isinstance(b, dict) and b.get("type") == "text"
                                ]
                                raw = "".join(text_parts)
                            try:
                                if isinstance(raw, str):
                                    carparks = json.loads(raw)
                                elif isinstance(raw, list):
                                    carparks = raw
                                else:
                                    carparks = None
                                log.debug(
                                    "[carpark_table] parsed type=%s len=%s",
                                    type(carparks).__name__,
                                    len(carparks) if isinstance(carparks, list) else "n/a",
                                )
                                if (
                                    isinstance(carparks, list)
                                    and carparks
                                    and isinstance(carparks[0], dict)
                                    and "carpark_id" in carparks[0]
                                ):
                                    yield f"data: {json.dumps({'type': 'carpark_table', 'carparks': carparks})}\n\n"
                            except (json.JSONDecodeError, TypeError, ValueError) as e:
                                log.debug(
                                    "[carpark_table] parse error: %s — raw=%r", e, str(raw)[:300]
                                )

                    # Log all chain_end events to discover the correct graph name
                    elif etype == "on_chain_end":
                        log.debug(
                            "[chain_end] name=%r keys=%s",
                            event["name"],
                            list(event["data"].get("output", {}).keys())
                            if isinstance(event["data"].get("output"), dict)
                            else type(event["data"].get("output")).__name__,
                        )

                    # Emit final response when the top-level graph finishes
                    if etype == "on_chain_end" and event["name"] == "LangGraph":
                        output = event["data"].get("output", {})
                        msgs = output.get("messages", [])
                        log.debug(
                            "[chain_end LangGraph] %d messages, types=%s",
                            len(msgs),
                            [getattr(m, "type", "?") for m in msgs],
                        )
                        for m in msgs:
                            log.debug(
                                "[msg] type=%r content=%r tool_calls=%r",
                                getattr(m, "type", "?"),
                                getattr(m, "content", "")[:80],
                                getattr(m, "tool_calls", None),
                            )
                        for m in reversed(msgs):
                            raw = getattr(m, "content", "")
                            if raw and getattr(m, "type", "") == "ai":
                                # content may be a string or a list of content blocks
                                if isinstance(raw, list):
                                    text = " ".join(
                                        (b.get("text", "") if isinstance(b, dict) else str(b))
                                        for b in raw
                                    )
                                else:
                                    text = str(raw)
                                yield f"data: {json.dumps({'type': 'response', 'text': text})}\n\n"
                                break

        except Exception as exc:
            log.exception("[exception] %s: %s", type(exc).__name__, exc)
            err_text = str(exc)
            # When the LLM calls a tool but the tool response has no content (e.g. MCP
            # subprocess crashed or returned None), give a friendlier message rather than
            # exposing the raw Anthropic validation error to the user.
            if "has no content" in err_text and "tool" in err_text.lower():
                err_text = (
                    "I need your current location to find nearby carparks. "
                    "Please enable location sharing in your browser (the location icon in the address bar) "
                    "and try again, or type your area name (e.g. \"Tampines\", \"Orchard\")."
                )
            yield f"data: {json.dumps({'type': 'error', 'text': err_text})}\n\n"
        finally:
            _langfuse_module.get_client().flush()

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
