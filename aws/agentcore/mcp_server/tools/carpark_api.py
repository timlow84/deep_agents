"""MCP tools for the LTA DataMall Carpark Availability v2 API.

Ported from tools/mcp/sg_carpark_mcp_server.py.
truststore and dotenv are omitted — env vars are injected by AgentCore at runtime.

Environment variables required:
    LTA_DATAMALL_BASE_URL  — e.g. https://datamall2.mytransport.sg/ltaodataservice
    LTA_DATAMALL_API_KEY   — AccountKey issued by LTA DataMall
"""

import logging
import math
import os
from typing import Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_LTA_BASE_URL = os.getenv("LTA_DATAMALL_BASE_URL", "").rstrip("/")
_LTA_API_KEY = os.getenv("LTA_DATAMALL_API_KEY", "")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 coordinates."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


async def _fetch_all_carparks() -> list[dict[str, Any]]:
    """Paginate through the OData endpoint — 500 records per page."""
    if not _LTA_BASE_URL or not _LTA_API_KEY:
        raise ValueError(
            "LTA_DATAMALL_BASE_URL and LTA_DATAMALL_API_KEY must be set"
        )

    headers = {"AccountKey": _LTA_API_KEY, "accept": "application/json"}
    records: list[dict[str, Any]] = []
    skip = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                f"{_LTA_BASE_URL}/CarParkAvailabilityv2",
                headers=headers,
                params={"$skip": skip},
            )
            resp.raise_for_status()
            batch = resp.json().get("value", [])
            records.extend(batch)
            if len(batch) < 500:
                break
            skip += 500

    logger.info("Fetched %d carpark records from LTA DataMall", len(records))
    return records


async def _find_nearby(
    lat: float,
    lon: float,
    radius_km: float,
    limit: int,
) -> list[dict[str, Any]]:
    all_carparks = await _fetch_all_carparks()
    nearby: list[dict[str, Any]] = []

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
                "carpark_id":     cp.get("CarParkID"),
                "development":    cp.get("Development"),
                "area":           cp.get("Area"),
                "lat":            cp_lat,
                "lon":            cp_lon,
                "available_lots": cp.get("AvailableLots"),
                "lot_type":       cp.get("LotType"),
                "agency":         cp.get("Agency"),
                "distance_km":    round(distance, 3),
            })

    nearby.sort(key=lambda x: x["distance_km"])
    return nearby[:limit]


# ── Tool registration ──────────────────────────────────────────────────────────

def register(mcp: FastMCP) -> None:
    """Register all LTA DataMall carpark tools on the MCP server instance."""

    @mcp.tool()
    async def get_nearby_carparks(
        lat: float,
        lon: float,
        radius_km: float = 1.0,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find the nearest Singapore carparks with real-time lot availability.

        Queries the LTA DataMall CarParkAvailabilityv2 API and returns carparks
        within the specified radius, sorted by distance from the given coordinates.

        Args:
            lat: Latitude of the search point (WGS84), e.g. 1.3521.
            lon: Longitude of the search point (WGS84), e.g. 103.8198.
            radius_km: Search radius in kilometres (default 1.0).
            limit: Maximum number of results to return (default 5, max 20).

        Returns:
            List of carparks sorted by ascending distance, each with:
              carpark_id    — LTA carpark identifier
              development   — Building / estate name
              area          — District area
              lat, lon      — Carpark coordinates
              available_lots — Real-time available lot count
              lot_type      — C = car, Y = motorcycle, H = heavy vehicle
              agency        — HDB / URA / LTA
              distance_km   — Distance from the search point
        """
        limit = min(limit, 20)
        return await _find_nearby(lat, lon, radius_km, limit)
