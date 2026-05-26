"""Carpark agent for Bedrock AgentCore using AWS Strands SDK.

Communicates with the main_agent via Agent-to-Agent (A2A) invocation.
Calls the LTA DataMall Carpark Availability API through the Bedrock AgentCore
Gateway (MCP protocol, AWS_IAM inbound auth with SigV4 signing).

Architecture:
    main_agent  ──A2A──▶  carpark_agent  ──MCP/SigV4──▶  AgentCore Gateway
                                                                  │
                                                                  ▼
                                                      LTA DataMall REST API

Environment variables required:
    GATEWAY_MCP_URL  — AgentCore Gateway MCP endpoint URL
                       default: the sg-carpark-gateway prod URL
    CLAUDE_MODEL     — Bedrock model ID (default: global.anthropic.claude-haiku-4-5-20251001-v1:0)
    AWS_REGION       — AWS region (default: ap-southeast-1)
"""

import json
import logging
import os
import re

import botocore.auth
import botocore.awsrequest
import botocore.session
import httpx
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_GATEWAY_MCP_URL = os.getenv(
    "GATEWAY_MCP_URL",
    "https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp",
)
_AWS_REGION  = os.getenv("AWS_REGION",   "ap-southeast-1")
_CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
)


# ── SigV4 auth for AgentCore Gateway ─────────────────────────────────────────
# The AgentCore Gateway uses AWS_IAM inbound authentication.  Every MCP request
# must be signed with SigV4 using service name "bedrock-agentcore".
# The IAM execution role (Bedrock_Role) must have:
#   bedrock-agentcore:InvokeGateway on the gateway ARN.

class _SigV4Auth(httpx.Auth):
    """httpx Auth that signs every request with AWS SigV4 for bedrock-agentcore."""

    def __init__(self, region: str = "ap-southeast-1") -> None:
        self._region = region
        # botocore session picks up credentials from:
        #   1. Environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
        #   2. IAM instance role / container credentials (preferred in AgentCore)
        #   3. ~/.aws/credentials (local development)
        self._bc_session = botocore.session.get_session()

    def auth_flow(self, request: httpx.Request):  # type: ignore[override]
        creds = self._bc_session.get_credentials()
        if creds is None:
            logger.warning("No AWS credentials found; sending unsigned MCP request")
            yield request
            return

        frozen = creds.get_frozen_credentials()

        # Build a botocore AWSRequest for signing
        aws_req = botocore.awsrequest.AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content or b"",
            headers=dict(request.headers),
        )

        signer = botocore.auth.SigV4Auth(frozen, "bedrock-agentcore", self._region)
        signer.add_auth(aws_req)

        # Copy signed headers (Authorization, X-Amz-Date, X-Amz-Security-Token)
        # back onto the httpx request
        for key, value in aws_req.headers.items():
            request.headers[key] = value

        yield request


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Singapore carpark availability assistant.

When asked to find carparks near a location:
1. Call get_nearby_carparks with the provided lat, lon, radius_km (default 1.0),
   and limit values.
2. Return the results as a structured JSON block followed by a brief summary.

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

After the JSON block, add a concise 1-2 sentence plain-text summary.
For non-carpark questions, respond normally as plain text (no JSON block).
"""


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(raw: str) -> dict:
    """Split agent output into optional structured carpark JSON + plain text."""
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if data.get("type") == "carparks":
                text = re.sub(r"```json.*?```\s*", "", raw, flags=re.DOTALL).strip()
                return {"output": text, "carparks": data.get("carparks", [])}
        except json.JSONDecodeError:
            pass
    return {"output": raw.strip(), "carparks": None}


# ── Invocation helper ─────────────────────────────────────────────────────────

def invoke(payload: dict) -> dict:
    """Called by the AgentCore HTTP handler (app.py) for every A2A request.

    The main_agent calls this via bedrock-agentcore:InvokeAgentRuntime with:
        {"lat": <float>, "lon": <float>, "limit": <int>, "sessionId": "..."}

    Returns:
        {
            "output":    "plain-text summary",
            "carparks":  [ ...list of carpark dicts... ] | null,
            "sessionId": "..."
        }
    """
    lat        = float(payload.get("lat",   1.3521))
    lon        = float(payload.get("lon",   103.8198))
    limit      = int(payload.get("limit",   5))
    session_id = payload.get("sessionId",   "")
    # Also accept an explicit natural-language query from the caller
    input_text = payload.get("inputText",   "")

    limit = min(limit, 20)
    query = input_text or (
        f"Find the {limit} nearest carparks to coordinates "
        f"lat={lat:.6f}, lon={lon:.6f}"
    )

    logger.info(
        "Carpark agent invoked: lat=%.4f lon=%.4f limit=%d session=%s",
        lat, lon, limit, session_id,
    )

    # ── Connect to the AgentCore Gateway via MCP ──────────────────────────────
    # MCPClient is a synchronous context manager that manages the MCP session.
    # A new connection is created per request (stateless — safe for AgentCore).
    auth = _SigV4Auth(region=_AWS_REGION)
    model = BedrockModel(
        model_id=_CLAUDE_MODEL,
        region_name=_AWS_REGION,
    )

    with MCPClient(
        lambda: streamablehttp_client(_GATEWAY_MCP_URL, auth=auth)
    ) as mcp_client:
        # Discover the get_nearby_carparks tool exposed by the gateway
        tools = mcp_client.list_tools_sync()
        logger.info("Gateway tools discovered: %s", [t.tool_name for t in tools])

        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
        )

        raw_response = str(agent(query))

    parsed = _parse_response(raw_response)

    return {
        "output":    parsed["output"],
        "carparks":  parsed["carparks"],
        "sessionId": session_id,
    }
