"""Example: run the deep-agent orchestrator with the weather sub-agent."""
import asyncio
import os
import sys

import truststore

truststore.inject_into_ssl()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from agents.main_agent import build_main_agent  # noqa: E402
from agents.weather_agent import (build_weather_subagent,  # noqa: E402
                                  get_weather_mcp_config)


async def main():
    client = MultiServerMCPClient(get_weather_mcp_config())
    tools = await client.get_tools()
    weather_subagent = build_weather_subagent(tools)
    graph = build_main_agent(weather_subagent)

        query = "What is the current weather in London?"
        print(f"Query: {query}\n")

        result = await graph.ainvoke({"messages": [HumanMessage(content=query)]})

        for msg in result["messages"]:
            if getattr(msg, "type", "") == "ai" and msg.content:
                print(f"[assistant]: {msg.content}\n")


if __name__ == "__main__":
    asyncio.run(main())
