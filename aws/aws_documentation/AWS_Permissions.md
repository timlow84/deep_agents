# AWS IAM Permissions Reference

> **Account:** `373447294617`  
> **Region:** `ap-southeast-1`  
> **Last verified:** 2026-05-27 — full stack working end-to-end (all blocking gaps resolved)

This document describes every IAM permission required between AWS components in the
**Deep Agents** weather + Singapore carpark chatbot architecture, including the reason
each permission is needed. All permissions have been verified against the live account.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser                                                                 │
│  S3 static site  ──→  Amazon Cognito  (PKCE login)                      │
│       │                                                                  │
│       └──→  API Gateway  POST /chat                                      │
│                  │                                                       │
│                  └──→  Lambda (invoke-agentcore)                         │
│                              │                                           │
│                              └──→  main_agent  (AgentCore Runtime)      │
│                                         │                                │
│                                         ├──→  Secrets Manager            │
│                                         ├──→  Amazon Bedrock (LLM)      │
│                                         ├──→  MCP Server / weather ECR  │
│                                         └──→  carpark_agent  (A2A)      │
│                                                    │                     │
│                                                    ├──→ Secrets Manager  │
│                                                    ├──→ Amazon Bedrock   │
│                                                    └──→ AgentCore Gateway│
│                                                              │           │
│                                                              └──→ LTA    │
│                                                                  DataMall│
└──────────────────────────────────────────────────────────────────────────┘
```

**Key resources:**

| Resource | ARN / ID |
|---|---|
| S3 Bucket | `arn:aws:s3:::svc-agentcore` |
| Cognito User Pool | `ap-southeast-1_9AT4o3r3W` |
| API Gateway | `https://p388lb687k.execute-api.ap-southeast-1.amazonaws.com` |
| Lambda Function | `arn:aws:lambda:ap-southeast-1:373447294617:function:invoke-agentcore` |
| AgentCore Runtime — main_agent | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM` |
| AgentCore Runtime — carpark_agent | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd` |
| Secrets Manager — app keys | `arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp` |
| Secrets Manager — LTA API key | `arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98` |
| ECR — Main Agent | `373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent` |
| ECR — MCP Server | `373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/mcp-server` |
| ECR — Carpark Agent | `373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent` |
| IAM Execution Role | `arn:aws:iam::373447294617:role/Bedrock_Role` |
| **AgentCore Gateway** | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc` |
| **Gateway MCP URL** | `https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp` |
| **Gateway Target** | ID `D6NDMNRBLS` — `lta-carpark-rest-api` (OpenAPI schema from S3) |
| **API Key Credential Provider** | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:token-vault/default/apikeycredentialprovider/lta-datamall-api-key` |
| **OpenAPI Schema (S3)** | `s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml` |
| **Lambda Interceptor** | `arn:aws:lambda:ap-southeast-1:373447294617:function:lta-datamall-api-interceptor` |

---

## IAM Role: `Bedrock_Role`

A **single shared execution role** used by Lambda, Bedrock AgentCore, and Bedrock itself.
It is assumed by three different AWS service principals depending on which component is
making the call.

### Trust Policy (who can assume this role)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::373447294617:root" },
      "Action": "sts:AssumeRole"
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock.amazonaws.com" },
      "Action": "sts:AssumeRole"
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

| Principal | Reason |
|---|---|
| `arn:aws:iam::373447294617:root` | Allows any IAM principal in the account (e.g. admin users) to assume the role for manual testing or console operations. |
| `lambda.amazonaws.com` | Required so the Lambda service can assume this role at invocation time and use it as the function's execution role. Without this, Lambda returns `"The role defined for the function cannot be assumed by Lambda"` and refuses to create/run the function. |
| `bedrock.amazonaws.com` | Allows Amazon Bedrock to assume this role when making cross-service calls on behalf of Bedrock workloads (e.g. model invocation initiated by AgentCore). |
| `bedrock-agentcore.amazonaws.com` | Allows the AgentCore Runtime to assume this role for its container execution. AgentCore uses it to call Bedrock for LLM inference and Secrets Manager for API keys. |

---

## Permissions By Component Flow

### 1. Browser → S3 (Static Site Hosting)

**Resource:** `arn:aws:s3:::svc-agentcore/public/*`

**Policy type:** S3 Bucket Policy (resource-based, not IAM)

```json
{
  "Sid": "PublicReadPublicPrefix",
  "Effect": "Allow",
  "Principal": "*",
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::svc-agentcore/public/*"
}
```

| Permission | Reason |
|---|---|
| `s3:GetObject` on `public/*` | Allows any anonymous browser to download `index.html`, `oidc-client-ts.min.js`, and `claudecode-color.png`. Without this the browser gets `403 AccessDenied` and the chatbot page fails to load. Scoped to the `public/` prefix so files at the bucket root remain private. |

> **Note:** CORS is not yet configured on this bucket. If the site is served from a
> custom domain (not the S3 URL directly), a CORS rule allowing `GET` from that origin
> will be required — otherwise browser `fetch()` calls to API Gateway will be blocked
> by the browser's same-origin policy.

---

### 2. Browser → Amazon Cognito (Authentication)

**Resource:** Cognito User Pool `ap-southeast-1_9AT4o3r3W`  
**App Client:** `3fc64mf1pvufqoamndct9eqqfl` (`weather-chatbot-spa`)

Cognito authentication is handled entirely by the **Cognito Hosted UI** and the
`oidc-client-ts` library using **PKCE OAuth2**. No IAM permissions are required for
the browser — the interaction is over HTTPS to Cognito's public endpoints.

**Cognito configuration required (console, not IAM):**

| Setting | Value | Reason |
|---|---|---|
| Allowed callback URL | `https://svc-agentcore.s3.ap-southeast-1.amazonaws.com/public/index.html` | Cognito validates the `redirect_uri` in the PKCE code exchange. If the URL doesn't match exactly, Cognito rejects the login with `redirect_mismatch`. |
| Allowed sign-out URL | `https://svc-agentcore.s3.ap-southeast-1.amazonaws.com/public/index.html` | Required for the `/logout` endpoint to redirect back to the app after clearing the Cognito SSO cookie. |
| OAuth2 scopes | `phone openid email` | The `openid` scope is mandatory for OIDC (issues the ID token). `email` is used to display the username in the header after login. |
| Token validity | (default) | Access tokens are used by `oidc-client-ts` to gate the chat input; they don't currently pass a Bearer token to API Gateway. |

---

### 3. Browser → API Gateway (Chat Requests)

**Resource:** `https://p388lb687k.execute-api.ap-southeast-1.amazonaws.com/chat`

API Gateway is publicly accessible (no IAM auth on the endpoint itself — currently
open to all callers, authenticated only at the UI layer via Cognito).

**CORS headers returned by Lambda (`handler.py`):**

```python
_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}
```

| Header | Reason |
|---|---|
| `Access-Control-Allow-Origin: *` | Required so the browser allows the S3-hosted page to receive the API response. Restrict to the S3 origin URL in production. |
| `OPTIONS` method handler | The browser sends a CORS pre-flight `OPTIONS` request before `POST`. The Lambda returns `204` for `OPTIONS` so the pre-flight succeeds and the real request proceeds. |

> **Recommended hardening:** Add a Cognito Authorizer on the API Gateway `POST /chat`
> method so that requests without a valid Cognito ID token are rejected at the gateway
> before reaching Lambda.

---

### 4. API Gateway → Lambda (Function Invocation)

**Resource:** `arn:aws:lambda:ap-southeast-1:373447294617:function:invoke-agentcore`

API Gateway needs a **resource-based policy** on the Lambda function that allows
`lambda:InvokeFunction`. AWS creates this automatically when you configure the
integration in the console (Add Trigger → API Gateway) or via the CLI with
`aws lambda add-permission`.

**Required resource policy on the Lambda (auto-created by API Gateway integration):**

| Permission | Reason |
|---|---|
| `lambda:InvokeFunction` | Allows the API Gateway service principal (`apigateway.amazonaws.com`) to invoke the Lambda function when a `POST /chat` request arrives. Without this, API Gateway returns `500 Internal Server Error` or `403`. |

**Important implementation note — `runtimeSessionId` minimum length:**

The Lambda handler (`handler.py`) uses `context.aws_request_id` (always a 36-character
UUID) as the `runtimeSessionId` for `invoke_agent_runtime`. Do **not** pass the
user-provided `session_id` here — the user may supply a short string, and AgentCore
requires `runtimeSessionId` to be at least 33 characters.

```python
# Correct — always a 36-char UUID from the Lambda context
runtime_session_id = context.aws_request_id

# The user's session_id is passed inside the payload for conversation state,
# not as runtimeSessionId
agentcore_payload = json.dumps({
    "inputText": message,
    "sessionId": session_id,       # user-facing session (any length)
}).encode()

response = client.invoke_agent_runtime(
    agentRuntimeArn=_AGENTCORE_RUNTIME_ARN,
    payload=agentcore_payload,
    runtimeSessionId=runtime_session_id,  # must be >= 33 chars
)
```

---

### 5. Lambda → Bedrock AgentCore Runtime (main_agent)

**Role:** `Bedrock_Role` (Lambda execution role)  
**Inline policies:** `AgentCoreInvoke` + `AgentCoreInvokeRuntime`

AgentCore performs IAM checks at **two levels** — the runtime resource ARN and the
specific endpoint ARN. Both grants are required; omitting either causes `AccessDenied`.

```json
// AgentCoreInvoke — runtime-level check
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": [
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM",
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
  ]
}

// AgentCoreInvokeRuntime — endpoint-level check
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": [
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT",
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT"
  ]
}
```

| Permission | Reason |
|---|---|
| `bedrock-agentcore:InvokeAgentRuntime` on runtime ARN | Allows the Lambda (and main_agent) to call the AgentCore Runtime's top-level invoke endpoint. This is the base ARN check performed by the AgentCore control plane. |
| `bedrock-agentcore:InvokeAgentRuntime` on runtime-endpoint ARN | Allows targeting the specific `DEFAULT` runtime endpoint. AgentCore evaluates both the runtime ARN and the endpoint ARN — both grants are needed. |

Both policies include **both** main_agent and carpark_agent resource ARNs, because:
- The **Lambda** invokes main_agent directly.
- The **main_agent container** (running as `Bedrock_Role`) invokes carpark_agent via A2A.

---

### 5a. main_agent → carpark_agent (Agent-to-Agent / A2A)

**Role:** `Bedrock_Role` (main_agent's execution role inside the AgentCore container)

The main_agent calls carpark_agent using the `get_nearby_carparks_via_agent` `@tool`:

```python
client = boto3.client("bedrock-agentcore", region_name="ap-southeast-1")
response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd",
    payload=json.dumps({
        "lat":       round(lat, 6),
        "lon":       round(lon, 6),
        "limit":     min(limit, 20),
        "sessionId": f"carpark-{uuid.uuid4().hex}",
    }).encode(),
    runtimeSessionId=f"carpark-{uuid.uuid4().hex}",  # must be >= 33 chars (hex is 32 + prefix)
)
```

Since main_agent runs *as* `Bedrock_Role` inside AgentCore, the same role's
`AgentCoreInvoke` and `AgentCoreInvokeRuntime` policies (Section 5) already cover
this — both include the carpark_agent resource ARNs.

---

### 5b. carpark_agent → AgentCore Gateway (MCP / SigV4)

**Role:** `Bedrock_Role` (carpark_agent's execution role in AgentCore container)  
**Inline policy:** `AgentCoreInvokeGateway`

The carpark_agent calls the gateway using a SigV4-signed MCP connection:

```python
auth = _SigV4Auth(region="ap-southeast-1")   # botocore SigV4, service=bedrock-agentcore
with MCPClient(lambda: streamablehttp_client(GATEWAY_MCP_URL, auth=auth)) as mcp:
    tools = mcp.list_tools_sync()
    agent = Agent(model=model, tools=tools)
    response = str(agent(query))
```

Required policy (inline on `Bedrock_Role`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeGateway",
      "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc"
    }
  ]
}
```

| Permission | Reason |
|---|---|
| `bedrock-agentcore:InvokeGateway` | Required to send SigV4-signed MCP requests to the gateway endpoint. Requests that lack a valid signature or authorisation return `403 Forbidden`. |

---

### 6. Lambda → CloudWatch Logs (Execution Logs)

**Role:** `Bedrock_Role`  
**Attached managed policy:** `AWSLambdaBasicExecutionRole`
(`arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole`)

| Permission | Reason |
|---|---|
| `logs:CreateLogGroup` | Allows Lambda to create `/aws/lambda/invoke-agentcore` log group on first invocation if it doesn't exist. |
| `logs:CreateLogStream` | Allows Lambda to create a new log stream for each invocation batch. |
| `logs:PutLogEvents` | Allows Lambda to write `logger.info(...)` and `logger.error(...)` output to CloudWatch. Without this, all logging is silently dropped and debugging is impossible. |

---

### 7. AgentCore Runtime → Amazon Bedrock (LLM Inference)

**Role:** `Bedrock_Role`  
**Attached managed policy:** `AmazonBedrockFullAccess`
(`arn:aws:iam::aws:policy/AmazonBedrockFullAccess`)

| Permission (subset) | Reason |
|---|---|
| `bedrock:InvokeModel` | Allows the AgentCore container to call Claude models (e.g. `claude-haiku-4-5-20251001`) for LLM inference. This is the core permission — without it AgentCore cannot generate responses. |
| `bedrock:InvokeModelWithResponseStream` | Allows streaming token responses from Bedrock. Required if the agent uses streaming output. |
| `bedrock:ListFoundationModels` | Allows the agent to discover available models programmatically. |

> **Note:** `AmazonBedrockFullAccess` is broad. For production, scope this down to
> `bedrock:InvokeModel` on only the specific model ARNs the agent uses, e.g.:
> `arn:aws:bedrock:ap-southeast-1::foundation-model/anthropic.claude-haiku-*`

---

### 8. AgentCore Runtime → Secrets Manager (API Keys)

**Role:** `Bedrock_Role`  
**Inline policy:** `SecretsManagerAccess`

Both the main_agent and carpark_agent call `config.py → load_secrets()` at startup
to fetch API keys. Additionally, the AgentCore Gateway service reads the LTA DataMall
API key from a separate secret managed by the credential vault.

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": [
    "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp",
    "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98"
  ]
}
```

| Permission | Reason |
|---|---|
| `secretsmanager:GetSecretValue` on `deep-agents/api-keys-Cmsypp` | Allows `load_secrets()` in the AgentCore containers to fetch `OPENWEATHERMAP_API_KEY` and `ANTHROPIC_API_KEY` at container startup. Without this the container raises `RuntimeError` on boot. |
| `secretsmanager:GetSecretValue` on `bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98` | Required by the AgentCore Gateway's credential provider to retrieve the LTA DataMall `AccountKey`. The secret name uses the `!` separator — this is an AgentCore-managed secret; its exact ARN is assigned at credential provider creation time. Without this the gateway cannot authenticate outbound calls to LTA DataMall. |

**Secret contents:**

| Secret | Key | Purpose |
|---|---|---|
| `deep-agents/api-keys` | `OPENWEATHERMAP_API_KEY` | Weather tools — geocoding and current conditions |
| `deep-agents/api-keys` | `ANTHROPIC_API_KEY` | Direct Anthropic API calls (if not routing through Bedrock) |
| `bedrock-agentcore-identity!...` | `api_key_value` | LTA DataMall AccountKey (24-char key injected as `AccountKey` header) |

---

### 9. AgentCore Runtime → ECR (Container Image Pull)

**Role:** `Bedrock_Role`  
**Attached managed policy:** `AmazonEC2ContainerRegistryReadOnly`
(`arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly`)

| Permission | Reason |
|---|---|
| `ecr:GetAuthorizationToken` | Allows the AgentCore service to authenticate with ECR to pull images. Required before any image pull — without it ECR returns `401 Unauthorized`. |
| `ecr:BatchGetImage` | Allows pulling the image manifest from the repository. |
| `ecr:GetDownloadUrlForLayer` | Allows downloading each image layer from ECR. |
| `ecr:BatchCheckLayerAvailability` | Allows checking which layers are already cached before pulling. |

**ECR Repositories used:**

| Repository | Purpose |
|---|---|
| `deep-agents/main-agent` | Docker image for the Strands orchestrator agent that handles chat, weather (via MCP), and carpark (via A2A to carpark_agent). |
| `deep-agents/mcp-server` | Docker image for the MCP server that exposes weather tools (`geocode_city`, `get_current_weather`) to the main agent. |
| `deep-agents/carpark-agent` | Docker image for the carpark A2A sub-agent that connects to the AgentCore Gateway via SigV4-signed MCP to query LTA DataMall. |

---

### 10. AgentCore / Bedrock → Role Self-Pass (`iam:PassRole`)

**Role:** `Bedrock_Role`  
**Inline policy:** `PassBedrockRole`

```json
{
  "Effect": "Allow",
  "Action": "iam:PassRole",
  "Resource": "arn:aws:iam::373447294617:role/Bedrock_Role"
}
```

| Permission | Reason |
|---|---|
| `iam:PassRole` on `Bedrock_Role` | Required when creating or updating the AgentCore Runtime definition — the caller must prove they are allowed to pass the execution role to the AgentCore service. Without this, the `CreateAgentRuntime` API call fails with `AccessDenied: iam:PassRole`. Scoped to only `Bedrock_Role` itself for least-privilege. |

---

## Summary Table

| From | To | IAM Permission | Policy / Location |
|---|---|---|---|
| Anonymous Browser | S3 `public/*` | `s3:GetObject` | S3 Bucket Policy |
| Browser | Cognito Hosted UI | *(no IAM — public HTTPS)* | Cognito App Client config |
| API Gateway | Lambda | `lambda:InvokeFunction` | Lambda resource policy (auto) |
| Lambda | AgentCore Runtime (main) | `bedrock-agentcore:InvokeAgentRuntime` | Inline: `AgentCoreInvoke` |
| Lambda | AgentCore Endpoint (main) | `bedrock-agentcore:InvokeAgentRuntime` | Inline: `AgentCoreInvokeRuntime` |
| Lambda | CloudWatch Logs | `logs:CreateLogGroup/Stream`, `logs:PutLogEvents` | Managed: `AWSLambdaBasicExecutionRole` |
| AgentCore main_agent | Bedrock (LLM) | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | Managed: `AmazonBedrockFullAccess` |
| AgentCore main_agent | Secrets Manager | `secretsmanager:GetSecretValue` | Inline: `SecretsManagerAccess` |
| AgentCore main_agent | ECR | `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | Managed: `AmazonEC2ContainerRegistryReadOnly` |
| AgentCore main_agent | carpark_agent (A2A) | `bedrock-agentcore:InvokeAgentRuntime` | Inline: `AgentCoreInvoke` + `AgentCoreInvokeRuntime` ✓ |
| AgentCore carpark_agent | Bedrock (LLM) | `bedrock:InvokeModel` | Managed: `AmazonBedrockFullAccess` |
| AgentCore carpark_agent | Secrets Manager | `secretsmanager:GetSecretValue` | Inline: `SecretsManagerAccess` |
| AgentCore carpark_agent | AgentCore Gateway | `bedrock-agentcore:InvokeGateway` | Inline: `AgentCoreInvokeGateway` ✓ |
| AgentCore Gateway (exec role) | Workload Identity token | `bedrock-agentcore:GetWorkloadAccessToken` | Inline: `AgentCoreGatewayWorkloadIdentity` ✓ |
| AgentCore Gateway (exec role) | API key credential vault | `bedrock-agentcore:GetResourceApiKey` | Inline: `AgentCoreGatewayWorkloadIdentity` ✓ |
| AgentCore Gateway (exec role) | LTA API key secret | `secretsmanager:GetSecretValue` | Inline: `SecretsManagerAccess` ✓ |
| AgentCore / Admin | Role self-pass | `iam:PassRole` | Inline: `PassBedrockRole` |

---

## AgentCore Gateway: `sg-carpark-gateway` (MCP / IAM Inbound Auth)

The gateway exposes the **Singapore Carpark Availability API** (LTA DataMall
`CarParkAvailabilityv2`) as a managed MCP endpoint. Any agent or SDK client
with the `bedrock-agentcore:InvokeGateway` permission can connect to it over
the MCP protocol without managing an MCP server themselves.

### Resources

| Resource | Value |
|---|---|
| Gateway ARN | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc` |
| Gateway ID | `sg-carpark-gateway-z3fc8wewlc` |
| MCP Endpoint URL | `https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp` |
| Protocol | MCP (versions `2025-06-18`, `2025-03-26`) |
| Inbound auth | `AWS_IAM` (SigV4-signed requests, service `bedrock-agentcore`) |
| Execution role | `arn:aws:iam::373447294617:role/Bedrock_Role` |
| Target ID | `D6NDMNRBLS` |
| Target name | `lta-carpark-rest-api` |
| Target type | REST API — OpenAPI schema from S3 |
| OpenAPI schema | `s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml` |
| Backend server | `https://datamall2.mytransport.sg/ltaodataservice` (LTA DataMall v2) |
| API Key credential provider | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:token-vault/default/apikeycredentialprovider/lta-datamall-api-key` |
| API Key secret ARN | `arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98` |

### Architecture

```
MCP Client (carpark_agent, SigV4-signed)
      │
      │  POST /mcp  (AWS_IAM inbound auth)
      ▼
AgentCore Gateway  sg-carpark-gateway-z3fc8wewlc
      │  Tool: lta-carpark-rest-api___get_nearby_carparks
      │  Credential: AccountKey header (from credential provider)
      ▼
LTA DataMall REST API  https://datamall2.mytransport.sg/ltaodataservice
      GET /CarParkAvailabilityv2   (AccountKey: <key>)
```

### 11. Caller → AgentCore Gateway (Inbound IAM Auth)

**Resource:** `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc`

Any IAM principal (user, role, Lambda, AgentCore runtime) that needs to call
the gateway must have the following **identity-based policy** attached:

```json
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeGateway",
  "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc"
}
```

| Permission | Reason |
|---|---|
| `bedrock-agentcore:InvokeGateway` | Required to send MCP requests to the gateway endpoint. Requests that do not carry a valid SigV4 signature from an authorised principal receive `403 Forbidden`. |

> **How to sign requests:** Use botocore `SigV4Auth` targeting service
> `bedrock-agentcore`, region `ap-southeast-1`. See
> [`carpark_agent.py`](../agentcore/agents/carpark_agent/carpark_agent.py)
> for the `_SigV4Auth(httpx.Auth)` implementation, or
> [`test_gateway.py`](../../../test_gateway.py) for a standalone test client.

---

### 12. Gateway → LTA DataMall REST API (Outbound API Key Injection)

The gateway authenticates outbound calls to the LTA DataMall API using the
`API_KEY` credential provider (`lta-datamall-api-key`). The key is injected
**directly** as the `AccountKey` header via the credential provider's
`credentialParameterName` / `credentialLocation` settings — no Lambda
interceptor is involved for this header injection.

**Gateway target credential provider configuration** (in
[create_gateway_target_input.json](../agentcore/gateway/create_gateway_target_input.json)):

```json
{
  "credentialProviderType": "API_KEY",
  "credentialProvider": {
    "apiKeyCredentialProvider": {
      "providerArn": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:token-vault/default/apikeycredentialprovider/lta-datamall-api-key",
      "credentialParameterName": "AccountKey",
      "credentialLocation": "HEADER"
    }
  }
}
```

| Field | Value | Reason |
|---|---|---|
| `credentialParameterName` | `AccountKey` | Name of the HTTP header to inject the API key into. LTA DataMall requires `AccountKey`; without it, all requests receive `404 "The requested API was not found"`. |
| `credentialLocation` | `HEADER` | Specifies the key goes in an HTTP request header (as opposed to `QUERY_PARAMETER`). |

> **Why not the Lambda interceptor?** The Lambda interceptor at the `REQUEST`
> interception point only receives **MCP protocol messages** (incoming from the
> MCP client, `event["mcp"]` format). It does **NOT** receive the outgoing HTTP
> target requests to LTA DataMall (`event["http"]` format was never triggered in
> testing). Therefore, any header renaming in the interceptor is dead code for
> the outbound path — use `credentialParameterName` instead.

**LTA DataMall auth behaviour:**

| Header sent | LTA DataMall response |
|---|---|
| `AccountKey: <valid-key>` | 200 + carpark data ✅ |
| `x-api-key: <key>` | 404 "The requested API was not found" |
| `Authorization: <key>` | 404 "The requested API was not found" |
| *(no auth header)* | 404 "The requested API was not found" |

LTA DataMall returns 404 (not 401/403) for any request that lacks a valid
`AccountKey` header — this is their convention for unauthenticated requests.

---

### 13. Gateway → Lambda Interceptor (REQUEST point — MCP passthrough)

The Lambda interceptor is still registered on the gateway and fires for every
incoming **MCP protocol message** (initialize, tools/list, tools/call from the
client). Its role is limited to **passthrough logging** for MCP messages; it
performs no header manipulation on outbound REST calls.

**Resource:** `arn:aws:lambda:ap-southeast-1:373447294617:function:lta-datamall-api-interceptor`

**Lambda resource policy** (grants `bedrock-agentcore.amazonaws.com` invoke rights
scoped to this specific gateway ARN):

```json
{
  "Sid": "AllowBedrockAgentCoreGatewayInvoke",
  "Effect": "Allow",
  "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
  "Action": "lambda:InvokeFunction",
  "Resource": "arn:aws:lambda:ap-southeast-1:373447294617:function:lta-datamall-api-interceptor",
  "Condition": {
    "ArnLike": {
      "AWS:SourceArn": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc"
    }
  }
}
```

| Permission | Reason |
|---|---|
| `lambda:InvokeFunction` (resource policy) | Allows the gateway service to call the interceptor Lambda synchronously on every REQUEST. The `SourceArn` condition prevents any other gateway from invoking this function. |
| `AWSLambdaBasicExecutionRole` (via `Bedrock_Role`) | Allows the Lambda to write execution logs to CloudWatch (`/aws/lambda/lta-datamall-api-interceptor`). |

**Interceptor event formats** — source at
[aws/agentcore/gateway/lta_datamall_api_interceptor.py](../agentcore/gateway/lta_datamall_api_interceptor.py):

| Event type | `event` key | Response key | Current behaviour |
|---|---|---|---|
| MCP protocol message (client→gateway) | `"mcp"` | `"mcp": {"transformedGatewayRequest": {"body": ...}}` | Pass body through unchanged ✓ |
| HTTP target request (gateway→LTA DataMall) | `"http"` | `"http": {"transformedGatewayRequest": {"headers": ...}}` | **Never triggered** — gateway handles header injection via `credentialParameterName` before reaching the interceptor |

**Critical response format:** the response key is `interceptorOutputVersion` (not
`interceptorInputVersion`), and the modified data goes under
`transformedGatewayRequest` (not `gatewayRequest`). Using the wrong keys causes
`"Received invalid response from interceptor"`.

To redeploy after code changes:

```bash
# Re-zip and update Lambda code
cd aws/agentcore/gateway
zip lta_datamall_api_interceptor.zip lta_datamall_api_interceptor.py
aws lambda update-function-code \
  --function-name lta-datamall-api-interceptor \
  --zip-file fileb://lta_datamall_api_interceptor.zip \
  --region ap-southeast-1
```

---

### 14. Gateway → Workload Identity (API Key Fetch)

**Role:** `Bedrock_Role` (gateway execution role)  
**Inline policy:** `AgentCoreGatewayWorkloadIdentity`

When a `tools/call` triggers an outbound REST request to LTA DataMall, the gateway
must retrieve the stored API key from the credential vault. This uses a two-step
workload identity mechanism:

1. Gateway fetches a short-lived workload identity token (`GetWorkloadAccessToken`)
2. Gateway uses the token to read the API key from the credential vault (`GetResourceApiKey`)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowGatewayWorkloadIdentityAndApiKey",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetResourceApiKey"
      ],
      "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:*"
    }
  ]
}
```

> **Why the wildcard resource?** AgentCore checks both `workload-identity-directory/*`
> and `token-vault/*` ARN patterns during the credential fetch flow. Scoping to either
> specific ARN results in `AccessDenied` on the other. Using
> `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:*` covers both patterns while
> remaining scoped to this account and region.

| Permission | Reason |
|---|---|
| `bedrock-agentcore:GetWorkloadAccessToken` | Allows the gateway execution role to fetch a short-lived workload token used to authenticate against the API key credential vault. Error without it: `"Failed to fetch outbound api key. Failed to get workload identity token - not authorized to perform: bedrock-agentcore:GetWorkloadAccessToken"`. |
| `bedrock-agentcore:GetResourceApiKey` | Allows the gateway to read the stored API key from the token vault. Required after `GetWorkloadAccessToken` succeeds — without it the credential fetch still fails. |

---

### OpenAPI Schema Notes

The OpenAPI schema source is committed at
[aws/agentcore/gateway/sg_carpark_openapi.yaml](../agentcore/gateway/sg_carpark_openapi.yaml).

**Key design constraints:**

| Constraint | Detail |
|---|---|
| No `$`-prefixed parameter names | Claude's tool input_schema requires property keys matching `^[a-zA-Z0-9_.-]{1,64}$`. OData's `$skip`, `$top`, `$filter` contain `$` and must not appear in the schema — even as optional parameters. Violating this causes `ValidationException` on `ConverseStream`. |
| Security scheme must match `credentialParameterName` | The schema's `securitySchemes` entry should use `name: AccountKey` to match the credential provider's injection. This is for documentation correctness; the actual header injection is driven by `credentialParameterName`, not the schema. |

Update and re-upload the schema whenever the API contract changes:

```bash
aws s3 cp aws/agentcore/gateway/sg_carpark_openapi.yaml \
  s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml \
  --region ap-southeast-1

# Then trigger a gateway target update to reload the schema
aws bedrock-agentcore-control update-gateway-target \
  --gateway-identifier sg-carpark-gateway-z3fc8wewlc \
  --target-id D6NDMNRBLS \
  --cli-input-json "file://aws/agentcore/gateway/create_gateway_target_input.json" \
  --region ap-southeast-1
```

---

## Recommended Hardening (non-blocking)

| Gap | Recommended Action | Priority |
|---|---|---|
| API Gateway → Cognito Authorizer | Add a Cognito Authorizer to the `POST /chat` method. Currently the API is open to unauthenticated callers at the network level; Cognito login is only enforced in the browser UI. | 🟡 Security |
| S3 CORS | Add a CORS rule allowing `GET` from the S3 origin if the site moves to a custom domain. | 🟡 Security |
| `AmazonBedrockFullAccess` scope | Replace with a least-privilege inline policy scoped to only the Claude model ARNs in use. | 🟢 Hardening |
| `AmazonS3ObjectLambdaExecutionRolePolicy` | Review if this policy attached to `Bedrock_Role` is still needed — the architecture does not appear to use S3 Object Lambda. Remove if unused. | 🟢 Hardening |
| CloudWatch Logs retention | Set a log retention policy on `/aws/lambda/invoke-agentcore` and `/aws/lambda/lta-datamall-api-interceptor` to avoid unbounded storage costs. | 🟢 Cost |
| AgentCore Gateway — `AgentCoreGatewayWorkloadIdentity` scope | Narrow `Resource: "*"` to the specific workload identity and token vault ARNs once stable (requires testing both ARN patterns). | 🟢 Hardening |

---

*Generated by Claude Code — verified against live AWS account `373447294617`.*  
*Gateway + interceptor added 2026-05-24. Carpark A2A + gateway credential fix 2026-05-27.*  
*Full stack (Browser → API GW → Lambda → main_agent → carpark_agent → Gateway → LTA DataMall) verified working 2026-05-27.*
