"""Placeholder API tools — replace with real API calls once your endpoints are ready.

How to extend:
    1. Add a new async function decorated with @mcp.tool() inside register().
    2. Use httpx.AsyncClient(base_url=_API_BASE_URL) to call your API.
    3. Write a thorough docstring — the LLM uses it to decide when and how to invoke the tool.
    4. Add type annotations on all parameters; FastMCP derives the JSON schema from them.

Environment variables consumed:
    API_BASE_URL   — Root URL for every API call, e.g.
                     https://abc123.execute-api.us-east-1.amazonaws.com/prod
    API_KEY        — Optional bearer token / API key forwarded in the Authorization header
"""

import logging
import os
from typing import Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_API_BASE_URL = os.getenv("API_BASE_URL", "")
_API_KEY = os.getenv("API_KEY", "")


def _headers() -> dict[str, str]:
    """Build common request headers."""
    h: dict[str, str] = {"Content-Type": "application/json"}
    if _API_KEY:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_API_BASE_URL,
        headers=_headers(),
        timeout=30,
    )


# ── Tool registration ──────────────────────────────────────────────────────────

def register(mcp: FastMCP) -> None:
    """Register all API tools on the MCP server instance."""

    @mcp.tool()
    async def ping() -> dict[str, Any]:
        """Check that the MCP server is running and the backing API is configured.

        Returns the server status and whether API_BASE_URL has been set.
        Use this to verify connectivity before calling other tools.
        """
        return {
            "status": "ok",
            "api_configured": bool(_API_BASE_URL),
            "api_base_url": _API_BASE_URL or "(not set — configure API_BASE_URL env var)",
        }

    @mcp.tool()
    async def get_resource(
        path: str,
        query_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a resource from the backing REST API via GET.

        Args:
            path: Endpoint path relative to API_BASE_URL, e.g. '/users' or '/orders/123'.
            query_params: Optional URL query parameters as a string-keyed dictionary.

        Returns:
            Parsed JSON response body from the API.
        """
        if not _API_BASE_URL:
            return {"error": "API_BASE_URL is not configured on the MCP server"}

        async with _client() as client:
            response = await client.get(path, params=query_params)
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    async def create_resource(
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a resource via POST to the backing REST API.

        Args:
            path: Endpoint path relative to API_BASE_URL, e.g. '/users' or '/orders'.
            body: Request body sent as JSON.

        Returns:
            Parsed JSON response body from the API.
        """
        if not _API_BASE_URL:
            return {"error": "API_BASE_URL is not configured on the MCP server"}

        async with _client() as client:
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    async def update_resource(
        path: str,
        body: dict[str, Any],
        method: str = "PUT",
    ) -> dict[str, Any]:
        """Update an existing resource via PUT or PATCH on the backing REST API.

        Args:
            path: Endpoint path, e.g. '/users/42' or '/orders/99'.
            body: Fields to update, sent as JSON.
            method: HTTP method to use — 'PUT' (full replace) or 'PATCH' (partial update).

        Returns:
            Parsed JSON response body from the API.
        """
        if not _API_BASE_URL:
            return {"error": "API_BASE_URL is not configured on the MCP server"}
        if method.upper() not in {"PUT", "PATCH"}:
            return {"error": f"method must be PUT or PATCH, got: {method}"}

        async with _client() as client:
            response = await client.request(method.upper(), path, json=body)
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    async def delete_resource(path: str) -> dict[str, Any]:
        """Delete a resource via DELETE on the backing REST API.

        Args:
            path: Endpoint path of the resource to delete, e.g. '/users/42'.

        Returns:
            Parsed JSON response body, or a confirmation message on 204 No Content.
        """
        if not _API_BASE_URL:
            return {"error": "API_BASE_URL is not configured on the MCP server"}

        async with _client() as client:
            response = await client.delete(path)
            response.raise_for_status()
            if response.status_code == 204:
                return {"deleted": True, "path": path}
            return response.json()
