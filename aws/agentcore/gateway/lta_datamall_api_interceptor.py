"""AgentCore Gateway REQUEST interceptor — renames the outbound API key header.

The AgentCore API key credential provider injects the stored key as the
standard ``x-api-key`` header.  The LTA DataMall v2 API expects the same
value under the ``AccountKey`` header.  This Lambda sits at the REQUEST
interception point and performs the rename transparently.

Interception point : REQUEST
Gateway            : sg-carpark-gateway-z3fc8wewlc
Target             : lta-carpark-rest-api  (LTA DataMall CarParkAvailabilityv2)

This Lambda handles TWO different event formats that arrive at the same
REQUEST interception point:

1. MCP protocol messages (client → gateway, path "/mcp")
   -----------------------------------------------------------
   Input  (interceptorInputVersion "1.0"):
     { "interceptorInputVersion": "1.0",
       "mcp": {
         "rawGatewayRequest": { "body": "<raw>" },
         "gatewayRequest":    { "path": "/mcp", "httpMethod": "POST",
                                "headers": {...}, "body": <parsed-dict> }
       }
     }
   Output (interceptorOutputVersion "1.0"):
     { "interceptorOutputVersion": "1.0",
       "mcp": {
         "transformedGatewayRequest": { "body": <same-or-modified-dict> }
       }
     }
   For MCP protocol messages (initialize, tools/list, tools/call) there is
   nothing to rename — we pass them through unchanged.

2. HTTP target requests (gateway → LTA DataMall REST API)
   -------------------------------------------------------
   Input  (interceptorInputVersion "1.0"):
     { "interceptorInputVersion": "1.0",
       "http": {
         "gatewayRequest": { "path": "/CarParkAvailabilityv2",
                             "httpMethod": "GET",
                             "headers": { "x-api-key": "<key>", ... },
                             "body": "<base64_encoded_body>" }
       }
     }
   Output (interceptorOutputVersion "1.0"):
     { "interceptorOutputVersion": "1.0",
       "http": {
         "transformedGatewayRequest": {
           "headers": { "AccountKey": "<key>", ... },
           "body":    "<base64_encoded_body>"
         }
       }
     }
   Here we rename x-api-key → AccountKey for LTA DataMall compatibility.

Returning {"action": "DENY"} anywhere aborts the request with 403.
"""

import json
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SOURCE_HEADER = "x-api-key"
_TARGET_HEADER = "AccountKey"


def handler(event: dict, context) -> dict:
    """Route the interceptor event to the correct handler.

    Handles interceptorInputVersion "1.0" in both MCP (path=/mcp) and
    HTTP target (path=/CarParkAvailabilityv2) formats.

    Args:
        event:   AgentCore Gateway REQUEST interceptor event.
        context: Lambda context (unused).

    Returns:
        Interceptor response in interceptorOutputVersion "1.0" format.
    """
    version = event.get("interceptorInputVersion", "unknown")
    logger.info(
        "REQUEST interceptor invoked — interceptorInputVersion=%s keys=%s",
        version,
        list(event.keys()),
    )
    logger.info("Full event: %s", json.dumps(event))

    if version != "1.0":
        # Unknown version — log and pass through as best-effort.
        logger.warning("Unknown interceptorInputVersion=%s — returning passthrough", version)
        return _mcp_passthrough(event.get("mcp", {}).get("gatewayRequest", {}).get("body", {}))

    # ── MCP protocol message (incoming from client to gateway) ───────────────
    if "mcp" in event:
        return _handle_mcp(event)

    # ── HTTP target request (outgoing from gateway to LTA DataMall API) ──────
    if "http" in event:
        return _handle_http(event)

    logger.warning("Event has neither 'mcp' nor 'http' key — returning minimal passthrough")
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {}}}


# ── MCP handler ───────────────────────────────────────────────────────────────

def _handle_mcp(event: dict) -> dict:
    """Handle incoming MCP protocol message (path=/mcp).

    For MCP messages (initialize, tools/list, tools/call), the API key is not
    yet present — the credential provider injects it into the outgoing HTTP
    request later.  We pass the body through unchanged.
    """
    mcp = event.get("mcp") or {}
    gateway_request = mcp.get("gatewayRequest") or {}
    body = gateway_request.get("body")

    method = body.get("method", "unknown") if isinstance(body, dict) else "unknown"
    logger.info("MCP REQUEST interceptor — method=%s path=%s", method, gateway_request.get("path"))

    return _mcp_passthrough(body)


def _mcp_passthrough(body) -> dict:
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body": body,
            }
        }
    }


# ── HTTP target handler ───────────────────────────────────────────────────────

def _handle_http(event: dict) -> dict:
    """Handle outgoing HTTP request to the LTA DataMall REST API.

    Renames the ``x-api-key`` header to ``AccountKey`` so the LTA DataMall
    v2 API can authenticate the request.  The API key is injected by the
    AgentCore Gateway API key credential provider as ``x-api-key`` just
    before this interceptor is called.
    """
    http = event.get("http") or {}
    gateway_request = http.get("gatewayRequest") or {}
    path = gateway_request.get("path", "")
    raw_headers = gateway_request.get("headers") or {}
    body = gateway_request.get("body")  # base64-encoded string

    logger.info("HTTP REQUEST interceptor — path=%s method=%s", path, gateway_request.get("httpMethod"))

    # Rename x-api-key → AccountKey (case-insensitive match).
    renamed = False
    new_headers: dict = {}
    for name, value in raw_headers.items():
        if name.lower() == _SOURCE_HEADER:
            new_headers[_TARGET_HEADER] = value
            renamed = True
            logger.info(
                "Renamed header '%s' → '%s' for LTA DataMall compatibility (path=%s)",
                name, _TARGET_HEADER, path,
            )
        else:
            new_headers[name] = value

    if not renamed:
        logger.warning(
            "Header '%s' not found in HTTP request headers for path=%s; "
            "'%s' will not be injected.  Ensure the API key credential provider "
            "is configured on the gateway target.",
            _SOURCE_HEADER, path, _TARGET_HEADER,
        )

    transformed: dict = {"headers": new_headers}
    if body is not None:
        transformed["body"] = body  # pass base64-encoded body unchanged

    response = {
        "interceptorOutputVersion": "1.0",
        "http": {
            "transformedGatewayRequest": transformed,
        }
    }
    logger.info("Returning HTTP response: %s", json.dumps(response))
    return response
