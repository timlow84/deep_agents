# AWS IAM Permissions Reference

> **Account:** `373447294617`  
> **Region:** `ap-southeast-1`  
> **Last verified:** 2026-05-27 (full stack working end-to-end: gateway AccountKey fix, $skip removed, Lambda runtimeSessionId fix)

This document describes every IAM permission required between AWS components in the
**Deep Agents** weather-chatbot architecture, including the reason each permission is
needed. Permissions were verified against the live account using the AWS CLI.

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
| Secrets Manager | `arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp` |
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

---

### 5. Lambda → Bedrock AgentCore Runtime (main_agent)

**Role:** `Bedrock_Role` (Lambda execution role)  
**Inline policies:** `AgentCoreInvoke` + `AgentCoreInvokeRuntime`

```json
// AgentCoreInvoke — NEEDS UPDATE (see below)
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": [
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM",
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
  ]
}

// AgentCoreInvokeRuntime — NEEDS UPDATE (see below)
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": [
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT",
    "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT"
  ]
}
```

> ⚠️ **Action required (manual):** The IAM user `timothylowaws` lacks `iam:PutRolePolicy`.
> An AWS account admin must update both inline policies on `Bedrock_Role` to include the
> carpark_agent resource ARNs shown above. Without this, main_agent's A2A call to
> carpark_agent returns `AccessDenied`.

| Permission | Reason |
|---|---|
| `bedrock-agentcore:InvokeAgentRuntime` on runtime ARN | Allows the Lambda to call the AgentCore Runtime's top-level invoke endpoint. This is the base ARN check performed by the AgentCore control plane. |
| `bedrock-agentcore:InvokeAgentRuntime` on runtime-endpoint ARN | Allows the Lambda to target the specific `DEFAULT` runtime endpoint. AgentCore evaluates both the runtime ARN and the endpoint ARN — both grants are needed. |

> **Why two separate inline policies?** AgentCore performs IAM checks at two levels:
> the runtime resource and the specific endpoint resource. Granting only one results in
> an `AccessDenied` from the other level.

---

### 5a. main_agent → carpark_agent (Agent-to-Agent / A2A)

**Role:** `Bedrock_Role` (main_agent's execution role in AgentCore container)  
**Required inline policy update:** Add to `AgentCoreInvoke`

The main_agent calls carpark_agent using the `get_nearby_carparks_via_agent` `@tool`:

```python
client = boto3.client("bedrock-agentcore", region_name="ap-southeast-1")
response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd",
    payload=json.dumps({...}).encode(),
    runtimeSessionId=f"carpark-{uuid4().hex[:8]}"
)
```

Since main_agent runs *as* `Bedrock_Role` inside AgentCore, the same role needs
`InvokeAgentRuntime` on the carpark_agent runtime ARN.

| Permission | Reason |
|---|---|
| `bedrock-agentcore:InvokeAgentRuntime` on carpark ARN | Allows the main_agent container (running as `Bedrock_Role`) to invoke carpark_agent as a downstream sub-agent. Without this, the A2A boto3 call fails with `AccessDenied`. |

---

### 5b. carpark_agent → AgentCore Gateway (MCP / SigV4)

**Role:** `Bedrock_Role` (carpark_agent's execution role in AgentCore container)  
**Required new inline policy:** `AgentCoreInvokeGateway`

The carpark_agent calls the gateway using a SigV4-signed MCP connection:

```python
auth = _SigV4Auth(region="ap-southeast-1")   # botocore SigV4
with MCPClient(lambda: streamablehttp_client(GATEWAY_MCP_URL, auth=auth)) as mcp:
    tools = mcp.list_tools_sync()
    ...
```

Required policy:

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

> ⚠️ **Action required (manual):** Add the above as a new inline policy named
> `AgentCoreInvokeGateway` on `Bedrock_Role`. Without this, the carpark_agent receives
> `403 Forbidden` from the gateway's IAM authoriser and cannot fetch carpark data.

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

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp"
}
```

| Permission | Reason |
|---|---|
| `secretsmanager:GetSecretValue` | Allows `config.py` (`load_secrets()`) in the AgentCore container to fetch the JSON secret containing `OPENWEATHERMAP_API_KEY` and `ANTHROPIC_API_KEY` at container startup. Without this, the container raises `RuntimeError` and fails to serve any requests. The resource is scoped to the exact secret ARN (including the random suffix `-Cmsypp`) for least-privilege. |

**Secret contents (`deep-agents/api-keys`):**

| Key | Purpose |
|---|---|
| `OPENWEATHERMAP_API_KEY` | Used by the MCP weather tools to call the OpenWeatherMap API for geocoding and current conditions. |
| `ANTHROPIC_API_KEY` | Used if the AgentCore container calls the Anthropic API directly (as opposed to routing through Bedrock). |

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
| AgentCore main_agent | carpark_agent (A2A) | `bedrock-agentcore:InvokeAgentRuntime` | Inline: `AgentCoreInvoke` ⚠️ **needs update** |
| AgentCore carpark_agent | Bedrock (LLM) | `bedrock:InvokeModel` | Managed: `AmazonBedrockFullAccess` |
| AgentCore carpark_agent | Secrets Manager | `secretsmanager:GetSecretValue` | Inline: `SecretsManagerAccess` |
| AgentCore carpark_agent | AgentCore Gateway | `bedrock-agentcore:InvokeGateway` | Inline: `AgentCoreInvokeRuntime` ✓ |
| AgentCore Gateway (execution role) | Workload Identity (API key vault) | `bedrock-agentcore:GetWorkloadAccessToken` | Inline: `AgentCoreGatewayWorkloadIdentity` ⚠️ **needs creation** |
| AgentCore / Admin | Role self-pass | `iam:PassRole` | Inline: `PassBedrockRole` |

---

## AgentCore Gateway: `sg-carpark-gateway` (MCP / IAM Inbound Auth)

The gateway exposes the **Singapore Carpark Availability API** (LTA DataMall
`CarParkAvailabilityv2`) as a managed MCP endpoint. Any agent or SDK client
with the `bedrock-agentcore:InvokeGateway` permission can connect to it over
the MCP protocol without managing the MCP server themselves.

### Resources

| Resource | Value |
|---|---|
| Gateway ARN | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc` |
| Gateway ID | `sg-carpark-gateway-z3fc8wewlc` |
| MCP Endpoint URL | `https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp` |
| Protocol | MCP (versions `2025-06-18`, `2025-03-26`) |
| Inbound auth | `AWS_IAM` (SigV4-signed requests) |
| Execution role | `arn:aws:iam::373447294617:role/Bedrock_Role` |
| Target ID | `D6NDMNRBLS` |
| Target name | `lta-carpark-rest-api` |
| Target type | REST API — OpenAPI schema from S3 |
| OpenAPI schema | `s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml` |
| Backend server | `https://datamall2.mytransport.sg/ltaodataservice` (LTA DataMall v2) |
| API Key credential provider | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:token-vault/default/apikeycredentialprovider/lta-datamall-api-key` |
| API Key secret ARN | `arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98-LMwdhV` |

### Architecture

```
MCP Client (agent / SDK)
      │
      │  POST /mcp  (SigV4 signed — AWS_IAM)
      ▼
AgentCore Gateway  sg-carpark-gateway-z3fc8wewlc
      │  Tool: get_nearby_carparks
      │  Credential: API key (AccountKey header)
      ▼
LTA DataMall REST API  https://datamall2.mytransport.sg/ltaodataservice
      GET /CarParkAvailabilityv2?$skip=…
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

> **How to sign requests:** Use the AWS SDK's SigV4 signer targeting service
> `bedrock-agentcore`, region `ap-southeast-1`. The
> `langchain-mcp-adapters` / `mcp` Python SDK can use `StreamableHTTPTransport`
> with a signed `httpx` client, or you can use `boto3` and append the
> `Authorization` header manually.

### 12. Gateway → LTA DataMall REST API (Outbound API Key + Header Translation)

The gateway authenticates outbound calls to the LTA DataMall API using the
`API_KEY` credential provider (`lta-datamall-api-key`), with a Lambda
interceptor handling header translation.

| Setting | Value |
|---|---|
| Credential provider type | `API_KEY` |
| Injected header (AgentCore default) | `x-api-key` |
| LTA expected header | `AccountKey` |
| Header translation | Lambda interceptor `lta-datamall-api-interceptor` at `REQUEST` point |

**Header translation flow:**

```
AgentCore Gateway (injects x-api-key)
      │
      ▼  REQUEST interception point
Lambda  lta-datamall-api-interceptor
      │  renames x-api-key → AccountKey
      ▼
LTA DataMall API  (receives AccountKey: <value>) ✓
```

### 13. Gateway → Lambda Interceptor (REQUEST point)

The gateway calls the header-translation Lambda before forwarding each request
to the LTA DataMall backend.

**Resource:** `arn:aws:lambda:ap-southeast-1:373447294617:function:lta-datamall-api-interceptor`

**Lambda resource policy** (grants `bedrock-agentcore.amazonaws.com` invoke rights
scoped to this specific gateway ARN — added via `lambda:AddPermission`):

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

**Interceptor logic** — source at
[aws/agentcore/gateway/lta_datamall_api_interceptor.py](../agentcore/gateway/lta_datamall_api_interceptor.py):

The interceptor receives TWO different event formats at the same REQUEST interception point:

| Event type | Trigger | `event` key | Response required |
|---|---|---|---|
| MCP protocol message | Incoming MCP `initialize` / `tools/list` / `tools/call` from carpark_agent | `"mcp"` | `{"interceptorOutputVersion":"1.0","mcp":{"transformedGatewayRequest":{"body":{...}}}}` |
| HTTP target request | Outgoing REST call from gateway to LTA DataMall | `"http"` | `{"interceptorOutputVersion":"1.0","http":{"transformedGatewayRequest":{"headers":{...}}}}` |

**Critical:** the response key is `interceptorOutputVersion` (not `interceptorInputVersion`),
and the modified data goes under `transformedGatewayRequest` (not `gatewayRequest`).

For MCP messages: the Lambda passes the body through unchanged.  
For HTTP target requests: the Lambda renames `x-api-key` → `AccountKey` in headers.

---

### 14. Gateway → Workload Identity (API Key Fetch)

**Role:** `Bedrock_Role` (gateway execution role)  
**Required new inline policy:** `AgentCoreGatewayWorkloadIdentity`

When a `tools/call` triggers an outbound REST request to LTA DataMall, the gateway
must retrieve the stored API key from the API key credential provider.  This uses the
workload identity token mechanism, which requires:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowGatewayWorkloadIdentityToken",
      "Effect": "Allow",
      "Action": "bedrock-agentcore:GetWorkloadAccessToken",
      "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:workload-identity-directory/default/workload-identity/sg-carpark-gateway-z3fc8wewlc"
    }
  ]
}
```

> ⚠️ **Action required (manual console):** Add the above as a new inline policy named
> `AgentCoreGatewayWorkloadIdentity` on `Bedrock_Role`.  
> Console path: **IAM → Roles → Bedrock_Role → Add permissions → Create inline policy → JSON tab**  
> Without this, every `tools/call` fails with:  
> `"Failed to fetch outbound api key. Failed to get workload identity token - not authorized to perform: bedrock-agentcore:GetWorkloadAccessToken"`

| Permission | Reason |
|---|---|
| `bedrock-agentcore:GetWorkloadAccessToken` on workload identity ARN | Allows the gateway execution role to fetch a short-lived workload token, which the gateway then uses to read the LTA DataMall API key from the credential vault. Without this, the gateway cannot authenticate outbound REST calls to LTA DataMall. |

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

### Local source file

The OpenAPI schema source is committed at
[aws/agentcore/gateway/sg_carpark_openapi.yaml](../agentcore/gateway/sg_carpark_openapi.yaml).
Update this file and re-upload to S3 whenever the API contract changes:

```bash
aws s3 cp aws/agentcore/gateway/sg_carpark_openapi.yaml \
  s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml \
  --region ap-southeast-1
```

---

## Permissions Not Yet Configured (Recommended)

| Gap | Recommended Action | Reason | Priority |
|---|---|---|---|
| **`AgentCoreInvoke` — add carpark ARN** | Update `AgentCoreInvoke` inline policy on `Bedrock_Role` to add `deepAgentsCarparkAgent-t3soUG2aAd` ARN | main_agent's A2A boto3 call to carpark_agent will return `AccessDenied` without this | 🔴 Blocking |
| **`AgentCoreInvokeRuntime` — add carpark endpoint** | Update `AgentCoreInvokeRuntime` inline policy to add `deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT` ARN | Required alongside the runtime ARN check | 🔴 Blocking |
| **`AgentCoreInvokeGateway` — new policy** | Add new inline policy `AgentCoreInvokeGateway` on `Bedrock_Role` with `bedrock-agentcore:InvokeGateway` on gateway ARN `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc` | carpark_agent's SigV4-signed MCP calls to the gateway will receive `403 Forbidden` without this | 🔴 Blocking |
| API Gateway → Cognito Authorizer | Add a Cognito Authorizer to the `POST /chat` method | Currently the API is open to unauthenticated callers at the network level. Cognito login is only enforced in the browser UI. | 🟡 Security |
| S3 CORS | Add a CORS rule allowing `GET` from the S3 origin | Required if the site moves to a custom domain or if any `fetch()` call targets S3 directly. | 🟡 Security |
| `AmazonBedrockFullAccess` scope | Replace with a least-privilege inline policy | Full Bedrock access grants all model invocation and management rights. Restrict to only the Claude model ARNs in use. | 🟢 Hardening |
| `AmazonS3ObjectLambdaExecutionRolePolicy` | Review if needed | This policy is attached to `Bedrock_Role` but the architecture does not appear to use S3 Object Lambda. Remove if unused to follow least-privilege. | 🟢 Hardening |
| CloudWatch Logs retention | Set a log retention policy on `/aws/lambda/invoke-agentcore` | By default Lambda log groups have no expiry, which incurs unbounded storage costs. | 🟢 Cost |
| Lambda interceptor CloudWatch log retention | Set retention on `/aws/lambda/lta-datamall-api-interceptor` | Without a retention policy the log group accumulates indefinitely. | 🟢 Cost |

**Steps to apply the 3 blocking IAM changes in the AWS Console:**

1. Go to **IAM → Roles → Bedrock_Role → Permissions** tab
2. Edit `AgentCoreInvoke` — change `Resource` from a string to a list:
   ```json
   "Resource": [
     "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM",
     "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
   ]
   ```
3. Edit `AgentCoreInvokeRuntime` — same change:
   ```json
   "Resource": [
     "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT",
     "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT"
   ]
   ```
4. Click **Add permissions → Create inline policy** → JSON editor:
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
   Name the policy `AgentCoreInvokeGateway` and save.

---

*Generated by Claude Code — verified against live AWS account `373447294617` on 2026-05-24. Gateway + interceptor added 2026-05-24. Carpark agent A2A + IAM gap analysis added 2026-05-25.*
