"""MCP server wrapping the LTA DataMall Carpark Availability v2 API."""

import math
import os
import sys

import httpx
import truststore
from dotenv import load_dotenv
from fastmcp import FastMCP

truststore.inject_into_ssl()

# Load .env from the project root (two levels up from this file) so the server
# works when spawned as a subprocess that doesn't inherit the parent's env.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

mcp = FastMCP("SG Carparks")


def _get_config() -> tuple[str, str]:
    base_url = os.getenv("LTA_DATAMALL_BASE_URL")
    api_key = os.getenv("LTA_DATAMALL_API_KEY")
    if not base_url:
        raise ValueError("LTA_DATAMALL_BASE_URL environment variable is not set")
    if not api_key:
        raise ValueError("LTA_DATAMALL_API_KEY environment variable is not set")
    return base_url.rstrip("/"), api_key


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 coordinates."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


async def _fetch_all_carparks(base_url: str, api_key: str) -> list[dict]:
    """Paginate through the OData endpoint (500 records per page)."""
    headers = {"AccountKey": api_key, "accept": "application/json"}
    records: list[dict] = []
    skip = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                f"{base_url}/CarParkAvailabilityv2",
                headers=headers,
                params={"$skip": skip},
            )
            resp.raise_for_status()
            batch = resp.json().get("value", [])
            records.extend(batch)
            if len(batch) < 500:
                break
            skip += 500
    return records


async def _find_nearby_carparks(
    lat: float,
    lon: float,
    radius_km: float = 2.0,
    limit: int = 5,
) -> list[dict]:
    """Core carpark lookup — importable without MCP for direct FastAPI calls."""
    base_url, api_key = _get_config()
    all_carparks = await _fetch_all_carparks(base_url, api_key)

    nearby: list[dict] = []
    for cp in all_carparks:
        location_str = cp.get("Location", "").strip()
        if not location_str:
            continue
        try:
            cp_lat, cp_lon = map(float, location_str.split())
        except ValueError:
            continue
        distance = _haversine_km(lat, lon, cp_lat, cp_lon)
        if distance <= radius_km:
            nearby.append({
                "carpark_id": cp.get("CarParkID"),
                "development": cp.get("Development"),
                "area": cp.get("Area"),
                "lat": cp_lat,
                "lon": cp_lon,
                "available_lots": cp.get("AvailableLots"),
                "lot_type": cp.get("LotType"),
                "agency": cp.get("Agency"),
                "distance_km": round(distance, 3),
            })

    nearby.sort(key=lambda x: x["distance_km"])
    return nearby[:limit]


@mcp.tool()
async def get_nearby_carparks(
    lat: float,
    lon: float,
    radius_km: float = 1.0,
    limit: int = 5,
) -> list[dict]:
    """Find the nearest Singapore carparks with real-time lot availability.

    Args:
        lat: Latitude of the search point (WGS84).
        lon: Longitude of the search point (WGS84).
        radius_km: Search radius in kilometres (default 1.0).
        limit: Maximum number of results to return (default 5).

    Returns a list of carparks sorted by distance, each with:
      carpark_id, development, area, lat, lon, available_lots,
      lot_type (C=car Y=motorcycle H=heavy), agency, distance_km.
    """
    return await _find_nearby_carparks(lat, lon, radius_km, limit)


if __name__ == "__main__":
    mcp.run()
