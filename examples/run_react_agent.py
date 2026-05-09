"""Example: run a single ReAct agent."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage  # noqa: E402

from agents import build_react_agent  # noqa: E402


def main():
    agent = build_react_agent()

    query = "What is 42 * 17, and what is the square root of that result?"
    print(f"Query: {query}\n")

    result = agent.invoke({"messages": [HumanMessage(content=query)]})

    for msg in result["messages"]:
        role = getattr(msg, "type", "unknown")
        print(f"[{role}]: {msg.content}\n")


if __name__ == "__main__":
    main()
