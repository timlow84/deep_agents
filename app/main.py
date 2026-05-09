"""FastAPI chatbot server — deepagent orchestrator + weather sub-agent via MCP."""

import logging
import os
import sys
from contextlib import asynccontextmanager

import truststore

truststore.inject_into_ssl()

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING").upper())

from agents.main_agent import build_main_agent  # noqa: E402
from agents.weather_agent import (build_weather_subagent,  # noqa: E402
                                  get_weather_mcp_config)

_STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(get_weather_mcp_config())
    tools = await client.get_tools()
    weather_subagent = build_weather_subagent(tools)
    app.state.graph = build_main_agent(weather_subagent, tools=tools)
    yield


app = FastAPI(title="Weather Chatbot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

from app.chat import router  # noqa: E402

app.include_router(router)


@app.get("/")
async def index():
    return FileResponse(os.path.join(_STATIC, "index.html"))
