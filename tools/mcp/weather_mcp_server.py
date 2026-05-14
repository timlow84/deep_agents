"""MCP server wrapping the OpenWeatherMap REST API (geocoding + current weather)."""

import datetime as _dt
import os
from collections import defaultdict as _defaultdict

import httpx
import truststore
from dotenv import load_dotenv
from fastmcp import FastMCP

truststore.inject_into_ssl()

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

BASE_URL = "https://api.openweathermap.org"

mcp = FastMCP("OpenWeatherMap")


def _api_key() -> str:
    key = os.getenv("OPENWEATHERMAP_API_KEY")
    if not key:
        raise ValueError("OPENWEATHERMAP_API_KEY environment variable is not set")
    return key


async def _geocode_city_impl(city: str) -> list[dict]:
    """Importable geocoding helper — bypasses MCP for direct FastAPI calls."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/geo/1.0/direct",
            params={"q": city, "limit": 5, "appid": _api_key()},
        )
        response.raise_for_status()
        return response.json()


async def _get_current_weather_impl(lat: float, lon: float, units: str = "metric") -> dict:
    """Importable weather helper — bypasses MCP for direct FastAPI calls."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/data/2.5/weather",
            params={"lat": lat, "lon": lon, "units": units, "appid": _api_key()},
        )
        response.raise_for_status()
        return response.json()


async def _get_forecast_impl(lat: float, lon: float, units: str = "metric") -> list[dict]:
    """5-day daily forecast aggregated from OWM 3-hour slots — bypasses MCP for direct calls."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/data/2.5/forecast",
            params={"lat": lat, "lon": lon, "units": units, "appid": _api_key()},
        )
        response.raise_for_status()
        data = response.json()

    today = _dt.date.today().isoformat()
    days: dict[str, list] = _defaultdict(list)
    for entry in data.get("list", []):
        date = entry["dt_txt"][:10]
        if date != today:
            days[date].append(entry)

    result = []
    for date, entries in sorted(days.items()):
        midday = next((e for e in entries if "12:00:00" in e["dt_txt"]), entries[0])
        result.append({
            "date": date,
            "day": _dt.date.fromisoformat(date).strftime("%a"),
            "icon": midday["weather"][0]["icon"],
            "description": midday["weather"][0]["description"],
            "temp_max": max(e["main"]["temp"] for e in entries),
            "temp_min": min(e["main"]["temp"] for e in entries),
        })

    return result[:5]


@mcp.tool()
async def get_5day_forecast(lat: float, lon: float, units: str = "metric") -> list[dict]:
    """Get a 5-day daily weather forecast for a latitude/longitude.

    Returns one entry per day (skipping today) with keys: date, day (short name),
    icon, description, temp_max, temp_min.
    units: 'metric' (°C), 'imperial' (°F), or 'standard' (K).
    """
    return await _get_forecast_impl(lat, lon, units)


@mcp.tool()
async def geocode_city(city: str) -> list[dict]:
    """Convert a city name to geographical coordinates. Returns up to 5 matches."""
    return await _geocode_city_impl(city)


@mcp.tool()
async def get_current_weather(lat: float, lon: float, units: str = "metric") -> dict:
    """Get current weather for a latitude/longitude.

    units: 'metric' (°C), 'imperial' (°F), or 'standard' (K).
    """
    return await _get_current_weather_impl(lat, lon, units)


if __name__ == "__main__":
    mcp.run()
