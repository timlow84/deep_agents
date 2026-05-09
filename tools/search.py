import os

from langchain_core.tools import tool


@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression. Input must be a valid Python math expression."""
    try:
        allowed = (
            {
                k: v
                for k, v in __builtins__.items()
                if k in ("abs", "round", "min", "max", "sum", "pow")
            }
            if isinstance(__builtins__, dict)
            else {}
        )
        result = eval(expression, {"__builtins__": allowed})  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error evaluating expression: {e}"


def get_tools() -> list:
    """Return the default tool set for agents. Web search included only if TAVILY_API_KEY is set."""
    tools = [calculator]
    if os.getenv("TAVILY_API_KEY"):
        from langchain_tavily import TavilySearch

        tools.insert(0, TavilySearch(max_results=5))
    return tools
