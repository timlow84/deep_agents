"""FastAPI HTTP server — entry point for the carpark AgentCore Runtime container.

Bedrock AgentCore Runtime invokes the container via HTTP:
  POST /invocations  — receives the A2A payload from main_agent, returns carpark data.
  GET  /ping         — liveness probe used by AgentCore.

The main_agent calls this via bedrock-agentcore:InvokeAgentRuntime with:
    {"lat": <float>, "lon": <float>, "limit": <int>, "sessionId": "..."}

Deploy as a container image; AgentCore manages scaling and routing.
"""

import logging

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# load_secrets() must run before carpark_agent is imported so that every
# os.getenv() call inside it (model ID, gateway URL) sees the injected values.
from config import load_secrets
load_secrets()

from carpark_agent import invoke

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Carpark AgentCore", version="1.0.0")


@app.get("/ping")
async def health():
    return {"status": "healthy"}


@app.post("/invocations")
async def handle_invoke(request: Request):
    """Receive an A2A invocation from the main_agent and return carpark data.

    Expected request body (sent by main_agent's get_nearby_carparks_via_agent tool):
        {
            "lat":       <float>,   // WGS84 latitude of the search point
            "lon":       <float>,   // WGS84 longitude of the search point
            "limit":     <int>,     // max carparks to return (default 5, max 20)
            "sessionId": "..."      // optional; echoed back in the response
        }

    Response body:
        {
            "output":    "plain-text summary",
            "carparks":  [ ...list of carpark dicts... ] | null,
            "sessionId": "..."
        }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Accept either structured {lat, lon} params or an inputText message
    if not payload.get("lat") and not payload.get("lon") and not payload.get("inputText"):
        raise HTTPException(
            status_code=400,
            detail="'lat' and 'lon' coordinates are required (or 'inputText' with coordinates)",
        )

    logger.info(
        "Carpark agent invocation: lat=%s lon=%s limit=%s session=%s",
        payload.get("lat"),
        payload.get("lon"),
        payload.get("limit", 5),
        payload.get("sessionId"),
    )

    try:
        result = invoke(payload)
    except Exception as exc:
        logger.exception("Carpark agent invocation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse(content=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
