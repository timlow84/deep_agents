"""Quick test — call the AgentCore Gateway MCP endpoint and print the full response.

Run with:  uv run python test_gateway.py
"""
import httpx
import botocore.auth
import botocore.awsrequest
import botocore.session

GATEWAY_URL = "https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp"
REGION = "ap-southeast-1"


class _SigV4Auth(httpx.Auth):
    def __init__(self, region: str = REGION) -> None:
        self._region = region
        self._bc_session = botocore.session.get_session()

    def auth_flow(self, request: httpx.Request):
        creds = self._bc_session.get_credentials()
        frozen = creds.get_frozen_credentials()
        aws_req = botocore.awsrequest.AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content or b"",
            headers=dict(request.headers),
        )
        botocore.auth.SigV4Auth(frozen, "bedrock-agentcore", self._region).add_auth(aws_req)
        for key, value in aws_req.headers.items():
            request.headers[key] = value
        yield request


_COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-03-26",
}


def _post(client: httpx.Client, payload: dict, session_id: str | None = None) -> httpx.Response:
    headers = dict(_COMMON_HEADERS)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return client.post(GATEWAY_URL, json=payload, headers=headers)


def _print(label: str, resp: httpx.Response) -> None:
    print(f"\n{'-'*60}")
    print(f"> {label}")
    print(f"  Status : {resp.status_code}")
    sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
    if sid:
        print(f"  Session: {sid}")
    print(f"  Body   : {resp.text[:500]}")


with httpx.Client(auth=_SigV4Auth(), timeout=20) as client:

    # 1. initialize
    resp = _post(client, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        },
    })
    _print("initialize", resp)
    session_id = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")

    # 2. initialized notification (required by MCP protocol)
    resp2 = _post(client, {
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, session_id)
    _print("notifications/initialized", resp2)

    # 3. tools/list
    resp3 = _post(client, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
    }, session_id)
    _print("tools/list", resp3)

    # Print FULL tools/list to see actual tool names
    import json as _json
    tl = _json.loads(resp3.text)
    tools = tl.get("result", {}).get("tools", [])
    print(f"\nTools available ({len(tools)}):")
    for t in tools:
        print(f"  - {t.get('name')}")

    if tools:
        tool_name = tools[0]["name"]
        # 4. tools/call — call first available tool near Orchard Road
        resp4 = _post(client, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {"lat": 1.3048, "lon": 103.8318, "limit": 3},
            },
        }, session_id)
        _print(f"tools/call ({tool_name})", resp4)

print("\nTest complete")
