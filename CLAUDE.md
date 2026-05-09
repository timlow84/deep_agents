# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Dos and Don't

- Do not remove/delete any comments or code sections which I have commented out!

## Commands

```bash
# Run the web server (development, with auto-reload)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run examples
uv run python examples/run_react_agent.py
uv run python examples/run_multi_agent.py
uv run python examples/run_main_agent.py   # DeepAgent + weather MCP

# Run tests (no API keys required)
uv run pytest tests/

# Run a single test
uv run pytest tests/test_tools.py::test_calculator -v

# Add a dependency
uv add <package>
```

## Architecture

This workspace demonstrates three agent patterns side by side, all sharing the same LLM factory (`agents/llm.py`) and tool infrastructure.

### The three agent patterns

| File | Pattern | Use case |
|------|---------|----------|
| `agents/main_agent.py` | DeepAgent orchestrator (`create_deep_agent`) | Delegates to sub-agents via built-in `task` tool |
| `agents/react_agent.py` | Single ReAct agent (`create_react_agent`) | Direct tool use (calculator, search) |
| `agents/multi_agent.py` | Supervisor graph | Two specialised agents (researcher → analyst) |

### DeepAgent + MCP wiring (the main production path)

The web server uses the DeepAgent pattern. The startup sequence in `app/main.py` lifespan is:

1. `MultiServerMCPClient(get_weather_mcp_config())` — spawns `tools/weather_mcp_server.py` as a subprocess via stdio
2. `await client.get_tools()` — discovers `geocode_city` and `get_current_weather` from the MCP server
3. `build_weather_subagent(tools)` — packages those tools into a `SubAgent` TypedDict
4. `build_main_agent(weather_subagent)` — calls `create_deep_agent(subagents=[weather_subagent])`, giving the orchestrator a built-in `task` tool it uses to delegate
5. `app.state.graph` — the compiled `CompiledStateGraph` is stored here and reused across all requests (no checkpointer, so each request is stateless)

### SSE streaming in `app/chat.py`

POST `/chat` returns a `StreamingResponse` of Server-Sent Events. The generator taps `graph.astream_events(..., version="v2")` and emits:
- `status` events on `on_tool_start` for known tool names (mapped via `_TOOL_LABELS`)
- `response` event on `on_chain_end` where `event["name"] == "LangGraph"` (the outermost graph completion)

AI message `content` may be a plain string **or** a list of content blocks (`[{"type": "text", "text": "..."}]`) — the handler normalises both.

### SSL patching for corporate networks (`agents/llm.py`)

`langchain-anthropic` v1.4.3 creates httpx clients internally without a `verify=` argument, so they use certifi's bundle, which does not include corporate proxy CAs. `agents/llm.py` monkey-patches `langchain_anthropic._client_utils` and `langchain_anthropic.chat_models` (both the source module **and** the direct import in `chat_models`) before any `ChatAnthropic` instance is created. The patch injects a `truststore.SSLContext` so the Windows certificate store is used.

`truststore.inject_into_ssl()` (called at the top of `app/main.py` before any imports) handles LangFuse's httpx client, which respects the global SSL patch.

Importing `agents.llm` applies the patch as a module-level side effect — this must happen before any `ChatAnthropic` is instantiated.

### LangFuse tracing

`app/chat.py` creates a `CallbackHandler()` per request (LangFuse v4 reads credentials from env vars, not constructor args). It wraps the `astream_events` call with `propagate_attributes(session_id=..., trace_name=...)` to tag every span with a unique session UUID and the user's message as the trace name. `langfuse.get_client().flush()` is called in the `finally` block.

### Models and environment

Default model: `claude-haiku-4-5-20251001` (set via `CLAUDE_MODEL` env var or `get_llm()` argument).

Required env vars: `ANTHROPIC_API_KEY`, `OPENWEATHERMAP_API_KEY`.  
Optional: `TAVILY_API_KEY` (enables web search tool in ReAct/multi-agent), `LANGFUSE_*` keys, `CLAUDE_MODEL`.

`uv` must use `link-mode = "copy"` (already set in `pyproject.toml`) due to an OS-level hardlink restriction on this machine.
