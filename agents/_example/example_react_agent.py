"""Single ReAct agent using LangGraph's prebuilt create_react_agent."""

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from tools import get_tools


def build_react_agent(
    model_name: str = "claude-sonnet-4-6",
    system_prompt: str = "You are a helpful AI assistant. Use tools when needed to answer questions accurately.",
):
    """Build a ReAct agent with the default tool set."""
    model = ChatAnthropic(model=model_name, temperature=0)
    tools = get_tools()
    return create_react_agent(model, tools, prompt=system_prompt)
