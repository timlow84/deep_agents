"""FastAPI HTTP server — entry point for the Bedrock AgentCore Runtime container.

Bedrock AgentCore Runtime invokes the container via HTTP. This server:
  POST /invoke  — receives the user payload, calls the Strands agent, returns the result.
  GET  /health  — liveness probe used by AgentCore.

Deploy as a container image; AgentCore manages scaling and routing.
"""

import logging

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# load_secrets() must run before main_agent is imported so that every
# os.getenv() call inside it (model ID, API keys) sees the injected values.
from config import load_secrets
load_secrets()

from main_agent import invoke

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Weather AgentCore", version="1.0.0")


@app.get("/ping")
async def health():
    return {"status": "healthy"}


@app.post("/invocations")
async def handle_invoke(request: Request):
    """Receive an invocation from Bedrock AgentCore and return the agent response.

    Expected request body (AgentCore standard format):
        {
            "inputText": "What's the weather in Tokyo?",
            "sessionId": "optional-session-id"
        }

    Response body:
        {
            "output": "plain-text summary",
            "weather": { ...structured weather data... } | null,
            "sessionId": "..."
        }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not payload.get("inputText") and not payload.get("message"):
        raise HTTPException(status_code=400, detail="'inputText' is required")

    logger.info("Invoking agent for session=%s", payload.get("sessionId"))

    try:
        result = invoke(payload)
    except Exception as exc:
        logger.exception("Agent invocation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse(content=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
