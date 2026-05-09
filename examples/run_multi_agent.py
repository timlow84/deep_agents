"""Example: run the supervisor multi-agent system."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage  # noqa: E402

from agents import build_supervisor  # noqa: E402


def main():
    graph = build_supervisor()

    query = "Research the latest trends in AI agents, then summarize the key themes in 3 bullet points."
    print(f"Query: {query}\n")

    result = graph.invoke({"messages": [HumanMessage(content=query)]})

    for msg in result["messages"]:
        name = getattr(msg, "name", None) or getattr(msg, "type", "unknown")
        if msg.content:
            print(f"[{name}]: {msg.content}\n")


if __name__ == "__main__":
    main()
