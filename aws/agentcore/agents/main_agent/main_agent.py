"""Main agent for Bedrock AgentCore using AWS Strands SDK.

Deployment: build as a container image and push to Bedrock AgentCore Runtime.
Entry point: app.py (FastAPI HTTP server that AgentCore calls via POST /invoke).

Environment variables required:
    OPENWEATHERMAP_API_KEY  — OpenWeatherMap API key
    AWS_REGION              — AWS region (default: us-east-1)
    CLAUDE_MODEL            — Bedrock model ID (default: us.anthropic.claude-haiku-4-5-20251001)
"""

import datetime
import json
import os
import re
from collections import defaultdict

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


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a weather assistant agent powered by OpenWeatherMap.

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

After the JSON block, add a concise 2-3 sentence plain-text summary suitable for chat.
For non-weather questions, respond normally as plain text (no JSON block).
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

_model = BedrockModel(
    model_id=os.getenv("CLAUDE_MODEL", "us.anthropic.claude-haiku-4-5-20251001"),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt=SYSTEM_PROMPT,
    tools=[geocode_city, get_current_weather, get_5day_forecast],
)


# ── Invocation helper ─────────────────────────────────────────────────────────

def _parse_agent_response(raw: str) -> dict:
    """Split agent output into optional structured weather JSON + plain text."""
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if data.get("type") == "weather":
                text = re.sub(r"```json.*?```\s*", "", raw, flags=re.DOTALL).strip()
                return {"text": text, "weather": data}
        except json.JSONDecodeError:
            pass
    return {"text": raw.strip(), "weather": None}


def invoke(payload: dict) -> dict:
    """Called by the AgentCore HTTP handler (app.py) for every request.

    Args:
        payload: dict with keys 'inputText' (required) and optionally 'sessionId'.

    Returns:
        dict with 'output' (plain text summary) and 'weather' (structured data or None).
    """
    message = payload.get("inputText", payload.get("message", ""))
    session_id = payload.get("sessionId")

    raw_response = str(agent(message))
    parsed = _parse_agent_response(raw_response)

    return {
        "output": parsed["text"],
        "weather": parsed["weather"],
        "sessionId": session_id,
    }
