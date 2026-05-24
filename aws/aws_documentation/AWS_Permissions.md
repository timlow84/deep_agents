# AWS IAM Permissions Reference

> **Account:** `373447294617`  
> **Region:** `ap-southeast-1`  
> **Last verified:** 2026-05-24

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

---

## Permissions Not Yet Configured (Recommended)

| Gap | Recommended Action | Reason |
|---|---|---|
| API Gateway → Cognito Authorizer | Add a Cognito Authorizer to the `POST /chat` method | Currently the API is open to unauthenticated callers at the network level. Cognito login is only enforced in the browser UI. |
| S3 CORS | Add a CORS rule allowing `GET` from the S3 origin | Required if the site moves to a custom domain or if any `fetch()` call targets S3 directly. |
| `AmazonBedrockFullAccess` scope | Replace with a least-privilege inline policy | Full Bedrock access grants all model invocation and management rights. Restrict to only the Claude model ARNs in use. |
| `AmazonS3ObjectLambdaExecutionRolePolicy` | Review if needed | This policy is attached to `Bedrock_Role` but the architecture does not appear to use S3 Object Lambda. Remove if unused to follow least-privilege. |
| CloudWatch Logs retention | Set a log retention policy on `/aws/lambda/invoke-agentcore` | By default Lambda log groups have no expiry, which incurs unbounded storage costs. |

---

*Generated by Claude Code — verified against live AWS account `373447294617` on 2026-05-24.*
