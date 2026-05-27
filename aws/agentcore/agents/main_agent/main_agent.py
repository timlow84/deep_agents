"""Main agent for Bedrock AgentCore using AWS Strands SDK.

Deployment: build as a container image and push to Bedrock AgentCore Runtime.
Entry point: app.py (FastAPI HTTP server that AgentCore calls via POST /invoke).

Environment variables required:
    OPENWEATHERMAP_API_KEY  — OpenWeatherMap API key
    AWS_REGION              — AWS region (default: ap-southeast-1)
    CLAUDE_MODEL            — Bedrock model ID (default: global.anthropic.claude-haiku-4-5-20251001-v1:0)

Optional environment variables:
    CARPARK_AGENT_ARN       — AgentCore Runtime ARN of the carpark sub-agent.
                              When set, enables A2A carpark lookups.
                              e.g. arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/carparkAgent-XXXXXXXXXX
"""

import datetime
import json
import os
import re
import uuid
from collections import defaultdict

import boto3
import httpx
from strands import Agent, tool
from strands.models import BedrockModel

_OWM_BASE = "https://api.openweathermap.org"


def _owm_key() -> str:
    key = os.environ.get("OPENWEATHERMAP_API_KEY", "")
    if not key:
        raise ValueError("OPENWEATHERMAP_API_KEY environment variable is not set")
    return key


# ── Weather tools ────────────────────────────────────────────────────────────

@tool
def geocode_city(city: str) -> list:
    """Convert a city name to geographical coordinates. Returns up to 5 matches with lat, lon, country."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{_OWM_BASE}/geo/1.0/direct",
            params={"q": city, "limit": 5, "appid": _owm_key()},
        )
        r.raise_for_status()
        return r.json()


@tool
def get_current_weather(lat: float, lon: float, units: str = "metric") -> dict:
    """Get current weather for a latitude/longitude coordinate.

    Args:
        lat: Latitude of the location.
        lon: Longitude of the location.
        units: 'metric' (°C, m/s), 'imperial' (°F, mph), or 'standard' (K).

    Returns a dict with keys: name, sys.country, main.temp, main.feels_like,
    main.humidity, wind.speed, weather[0].description, weather[0].icon, coord.lat, coord.lon.
    """
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{_OWM_BASE}/data/2.5/weather",
            params={"lat": lat, "lon": lon, "units": units, "appid": _owm_key()},
        )
        r.raise_for_status()
        return r.json()


@tool
def get_5day_forecast(lat: float, lon: float, units: str = "metric") -> list:
    """Get a 5-day daily weather forecast (one entry per day) for a lat/lon.

    Returns a list of dicts with keys: date, day (short weekday name),
    icon, description, temp_max, temp_min.
    """
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{_OWM_BASE}/data/2.5/forecast",
            params={"lat": lat, "lon": lon, "units": units, "appid": _owm_key()},
        )
        r.raise_for_status()
        data = r.json()

    today = datetime.date.today().isoformat()
    days: dict[str, list] = defaultdict(list)
    for entry in data.get("list", []):
        date = entry["dt_txt"][:10]
        if date != today:
            days[date].append(entry)

    result = []
    for date, entries in sorted(days.items()):
        midday = next((e for e in entries if "12:00:00" in e["dt_txt"]), entries[0])
        result.append({
            "date": date,
            "day": datetime.date.fromisoformat(date).strftime("%a"),
            "icon": midday["weather"][0]["icon"],
            "description": midday["weather"][0]["description"],
            "temp_max": max(e["main"]["temp"] for e in entries),
            "temp_min": min(e["main"]["temp"] for e in entries),
        })

    return result[:5]


# ── Carpark A2A tool ──────────────────────────────────────────────────────────

@tool
def get_nearby_carparks_via_agent(lat: float, lon: float, limit: int = 5) -> str:
    """Find nearby Singapore carparks with real-time lot availability.

    Delegates to the dedicated carpark AgentCore Runtime via Agent-to-Agent (A2A)
    invocation.  The carpark agent calls the LTA DataMall API through the
    Bedrock AgentCore Gateway and returns structured carpark JSON.

    Args:
        lat:   WGS84 latitude of the search point (Singapore: ~1.3521).
        lon:   WGS84 longitude of the search point (Singapore: ~103.8198).
        limit: Maximum number of carparks to return (default 5, max 20).

    Returns:
        JSON string with keys 'output' (text summary) and 'carparks' (list).
        Returns an error string if the carpark agent ARN is not configured or
        the A2A call fails.
    """
    carpark_arn = os.getenv("CARPARK_AGENT_ARN", "")
    if not carpark_arn:
        return (
            "Carpark agent is not configured. "
            "Set the CARPARK_AGENT_ARN environment variable on this AgentCore Runtime "
            "to enable real-time carpark lookups."
        )

    region = os.getenv("AWS_REGION", "ap-southeast-1")
    client = boto3.client("bedrock-agentcore", region_name=region)

    a2a_payload = json.dumps({
        "lat":       round(lat, 6),
        "lon":       round(lon, 6),
        "limit":     min(limit, 20),
        "sessionId": f"carpark-{uuid.uuid4().hex}",
    })

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=carpark_arn,
            payload=a2a_payload.encode(),
            runtimeSessionId=f"carpark-{uuid.uuid4().hex}",  # full 32-char hex → total 40 chars (min is 33)
        )

        raw = (
            response["response"].read()
            if hasattr(response.get("response", b""), "read")
            else response.get("response", b"")
        )
        return raw.decode()

    except Exception as exc:  # noqa: BLE001
        return f"Error calling carpark agent: {exc}"


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a weather and Singapore carpark assistant.

──────────────────────────────────────────────────────────────────
WEATHER QUERIES
──────────────────────────────────────────────────────────────────
When the user asks about weather for any city or location:
1. Call geocode_city to resolve the city name to coordinates.
2. Call get_current_weather with those coordinates to get live conditions.
3. Call get_5day_forecast with those coordinates for the 5-day forecast.
4. Respond with a JSON block followed by a short, friendly plain-text summary.

The JSON block MUST appear first and use this exact structure:
```json
{
  "type": "weather",
  "city": "<city name>",
  "country": "<ISO country code>",
  "lat": <latitude float>,
  "lon": <longitude float>,
  "temp": <current temp float>,
  "feels_like": <feels like float>,
  "humidity": <humidity int>,
  "wind_speed": <wind speed float>,
  "description": "<weather description>",
  "icon": "<OWM icon code e.g. 02d>",
  "forecast": [
    {"day": "Mon", "temp_max": <float>, "temp_min": <float>, "icon": "<code>", "description": "<desc>"},
    ...up to 5 days...
  ]
}
```

──────────────────────────────────────────────────────────────────
CARPARK QUERIES (Singapore only)
──────────────────────────────────────────────────────────────────
When the user asks about nearby carparks and provides GPS coordinates:
1. Extract the latitude, longitude, and number of carparks requested.
2. Call get_nearby_carparks_via_agent with those values.
3. Parse the returned JSON — it contains "output" (text) and "carparks" (list).
4. Respond with a JSON block followed by a brief plain-text summary.

The JSON block MUST appear first and use this exact structure:
```json
{
  "type": "carparks",
  "carparks": [
    {
      "carpark_id": "<id>",
      "development": "<name or null>",
      "area": "<area or null>",
      "lat": <float>,
      "lon": <float>,
      "available_lots": <int>,
      "lot_type": "<C|Y|H>",
      "agency": "<HDB|URA|LTA>",
      "distance_km": <float>
    }
  ]
}
```

──────────────────────────────────────────────────────────────────
For all other questions, respond normally as plain text (no JSON block).
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

# Built lazily on first request so env vars injected by load_secrets() are visible.
_agent: "Agent | None" = None


def _get_agent() -> "Agent":
    global _agent
    if _agent is None:
        model = BedrockModel(
            model_id=os.getenv("CLAUDE_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
            region_name=os.getenv("AWS_REGION", "ap-southeast-1"),
        )
        _agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=[
                geocode_city,
                get_current_weather,
                get_5day_forecast,
                get_nearby_carparks_via_agent,   # A2A → carpark sub-agent
            ],
        )
    return _agent


# ── Invocation helper ─────────────────────────────────────────────────────────

def _parse_agent_response(raw: str) -> dict:
    """Split agent output into optional structured JSON + plain text.

    Handles both 'weather' and 'carparks' response types.
    """
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            text = re.sub(r"```json.*?```\s*", "", raw, flags=re.DOTALL).strip()

            if data.get("type") == "weather":
                return {"text": text, "weather": data, "carparks": None}

            if data.get("type") == "carparks":
                return {"text": text, "weather": None, "carparks": data.get("carparks", [])}

        except json.JSONDecodeError:
            pass
    return {"text": raw.strip(), "weather": None, "carparks": None}


def invoke(payload: dict) -> dict:
    """Called by the AgentCore HTTP handler (app.py) for every request.

    Args:
        payload: dict with keys 'inputText' (required) and optionally 'sessionId'.

    Returns:
        dict with:
            'output'   — plain text summary
            'weather'  — structured weather data or None
            'carparks' — list of carpark dicts or None
            'sessionId'
    """
    message = payload.get("inputText", payload.get("message", ""))
    session_id = payload.get("sessionId")

    raw_response = str(_get_agent()(message))
    parsed = _parse_agent_response(raw_response)

    return {
        "output":    parsed["text"],
        "weather":   parsed["weather"],
        "carparks":  parsed["carparks"],
        "sessionId": session_id,
    }
