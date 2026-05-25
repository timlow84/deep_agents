# AWS IAM Permissions Reference

> **Account:** `373447294617`  
> **Region:** `ap-southeast-1`  
> **Last verified:** 2026-05-24 (gateway added 2026-05-24)

This document describes every IAM permission required between AWS components in the
**Deep Agents** weather-chatbot architecture, including the reason each permission is
needed. Permissions were verified against the live account using the AWS CLI.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser                                                            │
│  S3 static site  ──→  Amazon Cognito  (PKCE login)                 │
│       │                                                             │
│       └──→  API Gateway  POST /chat                                 │
│                  │                                                  │
│                  └──→  Lambda (invoke-agentcore)                    │
│                              │                                      │
│                              └──→  Bedrock AgentCore Runtime        │
│                                         │                           │
│                                         ├──→  Secrets Manager       │
│                                         ├──→  Amazon Bedrock (LLM) │
│                                         └──→  MCP Server (ECR)     │
└─────────────────────────────────────────────────────────────────────┘
```

**Key resources:**

| Resource | ARN / ID |
|---|---|
| S3 Bucket | `arn:aws:s3:::svc-agentcore` |
| Cognito User Pool | `ap-southeast-1_9AT4o3r3W` |
| API Gateway | `https://p388lb687k.execute-api.ap-southeast-1.amazonaws.com` |
| Lambda Function | `arn:aws:lambda:ap-southeast-1:373447294617:function:invoke-agentcore` |
| AgentCore Runtime | `arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM` |
| Secrets Manager | `arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp` |
| ECR — Main Agent | `373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent` |
| ECR — MCP Server | `373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/mcp-server` |
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

### 5. Lambda → Bedrock AgentCore Runtime

**Role:** `Bedrock_Role` (Lambda execution role)  
**Inline policies:** `AgentCoreInvoke` + `AgentCoreInvokeRuntime`

```json
// AgentCoreInvoke
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM"
}

// AgentCoreInvokeRuntime
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT"
}
```

| Permission | Reason |
|---|---|
| `bedrock-agentcore:InvokeAgentRuntime` on runtime ARN | Allows the Lambda to call the AgentCore Runtime's top-level invoke endpoint. This is the base ARN check performed by the AgentCore control plane. |
| `bedrock-agentcore:InvokeAgentRuntime` on runtime-endpoint ARN | Allows the Lambda to target the specific `DEFAULT` runtime endpoint. AgentCore evaluates both the runtime ARN and the endpoint ARN — both grants are needed. |

> **Why two separate inline policies?** AgentCore performs IAM checks at two levels:
> the runtime resource and the specific endpoint resource. Granting only one results in
> an `AccessDenied` from the other level.

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
| `deep-agents/main-agent` | Docker image for the LangGraph/Strands orchestrator agent that handles chat and calls Bedrock. |
| `deep-agents/mcp-server` | Docker image for the MCP server that exposes weather tools (`geocode_city`, `get_current_weather`) to the main agent. |

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
| Lambda | AgentCore Runtime | `bedrock-agentcore:InvokeAgentRuntime` | Inline: `AgentCoreInvoke` |
| Lambda | AgentCore Endpoint | `bedrock-agentcore:InvokeAgentRuntime` | Inline: `AgentCoreInvokeRuntime` |
| Lambda | CloudWatch Logs | `logs:CreateLogGroup/Stream`, `logs:PutLogEvents` | Managed: `AWSLambdaBasicExecutionRole` |
| AgentCore Runtime | Bedrock (LLM) | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | Managed: `AmazonBedrockFullAccess` |
| AgentCore Runtime | Secrets Manager | `secretsmanager:GetSecretValue` | Inline: `SecretsManagerAccess` |
| AgentCore Runtime | ECR | `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | Managed: `AmazonEC2ContainerRegistryReadOnly` |
| AgentCore / Admin | Role self-pass | `iam:PassRole` | Inline: `PassBedrockRole` |
| IAM caller | AgentCore Gateway | `bedrock-agentcore:InvokeGateway` | Caller's IAM policy |

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

1. Receives the full outbound request event (`passRequestHeaders: true`)
2. Searches headers (case-insensitive) for `x-api-key`
3. Replaces it with `AccountKey` preserving the value
4. Returns the modified event — AgentCore forwards the translated request to LTA DataMall

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

| Gap | Recommended Action | Reason |
|---|---|---|
| API Gateway → Cognito Authorizer | Add a Cognito Authorizer to the `POST /chat` method | Currently the API is open to unauthenticated callers at the network level. Cognito login is only enforced in the browser UI. |
| S3 CORS | Add a CORS rule allowing `GET` from the S3 origin | Required if the site moves to a custom domain or if any `fetch()` call targets S3 directly. |
| `AmazonBedrockFullAccess` scope | Replace with a least-privilege inline policy | Full Bedrock access grants all model invocation and management rights. Restrict to only the Claude model ARNs in use. |
| `AmazonS3ObjectLambdaExecutionRolePolicy` | Review if needed | This policy is attached to `Bedrock_Role` but the architecture does not appear to use S3 Object Lambda. Remove if unused to follow least-privilege. |
| CloudWatch Logs retention | Set a log retention policy on `/aws/lambda/invoke-agentcore` | By default Lambda log groups have no expiry, which incurs unbounded storage costs. |
| Gateway `InvokeGateway` for AgentCore Runtime | Add `bedrock-agentcore:InvokeGateway` to `Bedrock_Role` inline policy | Allows the main AgentCore agent runtime to call the SG Carpark gateway as a downstream tool. |
| `Bedrock_Role` S3 read for OpenAPI schema | Add `s3:GetObject` on `bedrock-agentcore-runtime-*/OpenAPI/*` to `Bedrock_Role` | The `timothylowaws` IAM user lacks `iam:PutRolePolicy`; an admin must add this inline policy so the gateway can refresh the schema from S3. |
| Lambda interceptor CloudWatch log retention | Set retention on `/aws/lambda/lta-datamall-api-interceptor` | Without a retention policy the log group accumulates indefinitely. |

---

*Generated by Claude Code — verified against live AWS account `373447294617` on 2026-05-24. Gateway + interceptor added 2026-05-25.*
