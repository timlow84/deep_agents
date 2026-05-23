"""Lambda function — API Gateway → Bedrock AgentCore Runtime → response.

This Lambda sits behind API Gateway and proxies chat requests to the
Bedrock AgentCore Runtime endpoint that hosts the Strands weather agent.

Required environment variables:
    AGENTCORE_RUNTIME_ARN  — ARN of the deployed AgentCore Runtime
                             e.g. arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/abc123
    AWS_REGION             — AWS region (auto-set by Lambda runtime)

API Gateway configuration:
    Method : POST /chat
    CORS   : Enable for the S3 website origin (or * for development)

Request body (from browser):
    {
        "message":    "What's the weather in Tokyo?",
        "history":    [{"role": "user", "content": "..."}, ...],  // optional
        "session_id": "uuid"                                       // optional
    }

Response body (to browser):
    {
        "text":       "plain-text agent summary",
        "weather":    { ...structured weather data... } | null,
        "session_id": "uuid"
    }
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_AGENTCORE_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# boto3 client for Bedrock AgentCore Runtime.
# Service name: 'bedrock-agentcore-runtime'
# Confirm the exact service name in the boto3 docs once your AgentCore Runtime is provisioned.
_agentcore_client = boto3.client("bedrock-agentcore-runtime", region_name=_AWS_REGION)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",           # restrict to your S3 website URL in production
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}


def _ok(body: dict) -> dict:
    return {"statusCode": 200, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def _err(status: int, message: str) -> dict:
    return {"statusCode": status, "headers": _CORS_HEADERS, "body": json.dumps({"error": message})}


def lambda_handler(event: dict, context) -> dict:
    # Handle CORS pre-flight
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 204, "headers": _CORS_HEADERS, "body": ""}

    # Parse request body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON body")

    message = (body.get("message") or "").strip()
    if not message:
        return _err(400, "'message' is required")

    session_id = body.get("session_id") or context.aws_request_id

    if not _AGENTCORE_RUNTIME_ARN:
        return _err(500, "AGENTCORE_RUNTIME_ARN environment variable is not set")

    # Invoke Bedrock AgentCore Runtime
    # The Runtime forwards the payload to the container's POST /invoke endpoint.
    agentcore_payload = json.dumps({
        "inputText": message,
        "sessionId": session_id,
    }).encode()

    try:
        logger.info("Invoking AgentCore runtime %s for session %s", _AGENTCORE_RUNTIME_ARN, session_id)

        # TODO: confirm the exact method name once the boto3 SDK for bedrock-agentcore-runtime
        # is available. Common candidates: invoke_agent_runtime / invoke_runtime / invoke.
        response = _agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=_AGENTCORE_RUNTIME_ARN,
            payload=agentcore_payload,
        )

        # The response body is a streaming blob; read it fully.
        raw = response["output"].read() if hasattr(response.get("output", b""), "read") else response.get("output", b"")
        agent_result = json.loads(raw)

    except _agentcore_client.exceptions.ClientError as exc:
        logger.error("AgentCore invocation error: %s", exc)
        return _err(502, f"AgentCore error: {exc.response['Error']['Message']}")
    except Exception as exc:
        logger.exception("Unexpected error invoking AgentCore: %s", exc)
        return _err(500, str(exc))

    return _ok({
        "text":       agent_result.get("output", ""),
        "weather":    agent_result.get("weather"),
        "session_id": session_id,
    })
