"""MCP server wrapping the OpenWeatherMap REST API (geocoding + current weather)."""

import os

import httpx
import truststore
from dotenv import load_dotenv
from fastmcp import FastMCP

truststore.inject_into_ssl()

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

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
