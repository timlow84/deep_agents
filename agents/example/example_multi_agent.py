"""Multi-agent supervisor pattern using LangGraph."""

from typing import Literal

from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from tools import get_tools

MEMBERS = ["researcher", "analyst"]
OPTIONS = MEMBERS + ["FINISH"]

SUPERVISOR_PROMPT = """You are a supervisor managing a team of AI agents: {members}.

Given the user request, decide which agent should act next, or respond FINISH when the task is complete.
- researcher: gathers information, searches the web, and retrieves facts
- analyst: processes gathered information, performs calculations, and synthesizes answers

Always route to FINISH when the task is fully answered."""


def build_supervisor(model_name: str = "claude-sonnet-4-6"):
    """Build a supervisor multi-agent graph."""
    llm = ChatAnthropic(model=model_name, temperature=0)
    tools = get_tools()

    researcher = create_react_agent(
        llm,
        tools,
        prompt="You are a research agent. Gather information and facts to answer questions. Be thorough.",
    )

    analyst = create_react_agent(
        llm,
        tools,
        prompt="You are an analyst. Process information, perform calculations, and synthesize clear answers.",
    )

    def agent_node(state: MessagesState, agent, name: str) -> dict:
        result = agent.invoke(state)
        last = result["messages"][-1]
        last.name = name
        return {"messages": [last]}

    def researcher_node(state: MessagesState) -> dict:
        return agent_node(state, researcher, "researcher")

    def analyst_node(state: MessagesState) -> dict:
        return agent_node(state, analyst, "analyst")

    supervisor_chain = llm.with_structured_output(
        schema={
            "title": "Route",
            "type": "object",
            "properties": {
                "next": {
                    "title": "Next",
                    "enum": OPTIONS,
                    "description": "Which agent to route to next, or FINISH.",
                }
            },
            "required": ["next"],
        }
    )

    def supervisor_node(state: MessagesState) -> dict:
        prompt = SUPERVISOR_PROMPT.format(members=", ".join(MEMBERS))
        messages = [{"role": "system", "content": prompt}] + state["messages"]
        result = supervisor_chain.invoke(messages)
        return {"messages": [], "next": result["next"]}

    class SupervisorState(MessagesState):
        next: str

    builder = StateGraph(SupervisorState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("researcher", researcher_node)
    builder.add_node("analyst", analyst_node)

    builder.add_edge(START, "supervisor")

    def route(state: SupervisorState) -> Literal["researcher", "analyst", "__end__"]:
        nxt = state.get("next", "FINISH")
        if nxt == "FINISH":
            return END
        return nxt

    builder.add_conditional_edges("supervisor", route)
    builder.add_edge("researcher", "supervisor")
    builder.add_edge("analyst", "supervisor")

    return builder.compile()
