"""AgentCore Gateway REQUEST interceptor — renames the outbound API key header.

The AgentCore API key credential provider injects the stored key as the
standard ``x-api-key`` header.  The LTA DataMall v2 API expects the same
value under the ``AccountKey`` header.  This Lambda sits at the REQUEST
interception point and performs the rename transparently.

Interception point : REQUEST
Gateway            : sg-carpark-gateway-z3fc8wewlc
Target             : lta-carpark-rest-api  (LTA DataMall CarParkAvailabilityv2)

Event schema (AgentCore Gateway REQUEST interceptor)
----------------------------------------------------
{
    "requestId":  "<uuid>",
    "gatewayId":  "sg-carpark-gateway-z3fc8wewlc",
    "targetId":   "D6NDMNRBLS",
    "httpMethod": "GET",
    "path":       "/CarParkAvailabilityv2",
    "queryStringParameters": { "$skip": "0" },
    "headers": {
        "x-api-key":    "<lta-account-key>",
        "Content-Type": "application/json",
        ...
    },
    "body": null
}

Return schema
-------------
Return the same structure with the modified ``headers`` dict.
Returning ``{"action": "DENY"}`` aborts the request with 403.
"""

import json
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SOURCE_HEADER = "x-api-key"
_TARGET_HEADER = "AccountKey"


def handler(event: dict, context) -> dict:
    """Rename ``x-api-key`` → ``AccountKey`` in outbound request headers.

    Args:
        event:   AgentCore Gateway REQUEST interceptor event.
        context: Lambda context (unused).

    Returns:
        The event with ``x-api-key`` renamed to ``AccountKey``.
        All other headers and fields are passed through unchanged.
    """
    logger.info("REQUEST interceptor invoked — requestId=%s", event.get("requestId"))
    logger.debug("Full event: %s", json.dumps(event))

    raw_headers: dict = event.get("headers") or {}

    # Build new headers dict, renaming the API key header (case-insensitive match).
    renamed = False
    new_headers: dict = {}
    for name, value in raw_headers.items():
        if name.lower() == _SOURCE_HEADER:
            new_headers[_TARGET_HEADER] = value
            renamed = True
            logger.info(
                "Renamed header '%s' → '%s' for LTA DataMall compatibility",
                name,
                _TARGET_HEADER,
            )
        else:
            new_headers[name] = value

    if not renamed:
        # x-api-key was absent — log a warning but do not block the request.
        # The downstream API will return 401 if the key is missing.
        logger.warning(
            "Header '%s' not found in request; '%s' will not be injected. "
            "Ensure the API key credential provider is configured on the gateway target.",
            _SOURCE_HEADER,
            _TARGET_HEADER,
        )

    # Return the full event with the modified headers — AgentCore continues
    # the request lifecycle with the returned payload.
    return {**event, "headers": new_headers}
