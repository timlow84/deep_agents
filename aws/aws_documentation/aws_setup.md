# AWS Setup Guide — Deep Agents (SG Carpark + Weather Chatbot)

> **Account:** `373447294617`  
> **Region:** `ap-southeast-1`  
> **Stack:** S3 → Cognito → API Gateway → Lambda → AgentCore (main_agent + carpark_agent) → AgentCore Gateway → LTA DataMall

This guide walks through creating every AWS component from scratch using the CLI.
It explains how each service connects to the next and why each IAM permission is required.

---

## How the Components Connect

```
Browser
  │
  ├─① GET static files
  │     S3 Bucket (svc-agentcore/public/)
  │
  └─② POST /chat  (JSON message)
        │
        ▼
    Amazon API Gateway  (HTTP API)
        │  triggers
        ▼
    Lambda  invoke-agentcore
        │  InvokeAgentRuntime
        ▼
    AgentCore Runtime — main_agent  (Docker container, ECR)
        │  reads API keys at startup
        ├─────────────────────────────────► Secrets Manager  (deep-agents/api-keys)
        │  calls LLM
        ├─────────────────────────────────► Amazon Bedrock  (Claude Haiku)
        │  weather tools (geocode, forecast)
        ├─────────────────────────────────► OpenWeatherMap API  (via httpx)
        │  A2A: InvokeAgentRuntime
        └─────────────────────────────────► AgentCore Runtime — carpark_agent  (Docker, ECR)
                                                │  reads API keys
                                                ├──────────────► Secrets Manager
                                                │  calls LLM
                                                ├──────────────► Amazon Bedrock
                                                │  SigV4-signed MCP
                                                └──────────────► AgentCore Gateway  sg-carpark-gateway
                                                                    │  retrieves API key from vault
                                                                    ├──► Secrets Manager  (AgentCore-managed)
                                                                    │  MCP intercept (tools/list, tools/call)
                                                                    ├──► Lambda  lta-datamall-api-interceptor
                                                                    │  HTTP GET with AccountKey header
                                                                    └──► LTA DataMall API
                                                                            /CarParkAvailabilityv2
```

**Connection mechanism for each hop:**

| Hop | Protocol | Authentication |
|---|---|---|
| Browser → API Gateway | HTTPS | None (public endpoint; Cognito login enforced in UI) |
| API Gateway → Lambda | AWS internal | Resource policy on Lambda (`lambda:InvokeFunction`) |
| Lambda → AgentCore Runtime | AWS SDK (`bedrock-agentcore:InvokeAgentRuntime`) | IAM role (`Bedrock_Role`) |
| main_agent → carpark_agent | AWS SDK (`bedrock-agentcore:InvokeAgentRuntime`) | Same IAM role (container inherits it) |
| carpark_agent → AgentCore Gateway | HTTPS + SigV4 | IAM role (`bedrock-agentcore:InvokeGateway`) |
| AgentCore Gateway → LTA DataMall | HTTPS | `AccountKey` header from credential vault |

---

## Prerequisites

```bash
# Verify AWS CLI is configured
aws sts get-caller-identity

# Expected output
{
    "UserId": "...",
    "Account": "373447294617",
    "Arn": "arn:aws:iam::373447294617:user/timothylowaws"
}

# Set a default region so you don't need --region on every command
export AWS_DEFAULT_REGION=ap-southeast-1          # Linux/macOS
$env:AWS_DEFAULT_REGION = "ap-southeast-1"        # PowerShell

# Docker must be installed and running (for ECR image builds)
docker --version

# AWS CLI v2 required for some bedrock-agentcore-control subcommands
aws --version
```

The IAM user running these commands needs permissions to create IAM roles, Lambda
functions, API Gateway, ECR repositories, S3 buckets, Cognito pools, Secrets Manager
secrets, and Bedrock AgentCore resources. An admin (`AdministratorAccess`) policy works
for initial setup.

---

## Step 1 — IAM Role (`Bedrock_Role`)

**Why a single shared role?**  
Lambda, AgentCore Runtime containers, and the AgentCore Gateway all need similar
permissions (Bedrock inference, Secrets Manager, ECR). Rather than creating three
separate roles that stay in sync, one role is shared across all components. The IAM
trust policy controls *which AWS service* is allowed to assume it.

**Why trust policies matter:**  
An IAM trust policy answers "who is allowed to wear this role?". Without the correct
service principal in the trust policy, the service gets `"The role cannot be assumed"`
and the deployment fails. Each service principal below has a specific reason:

```bash
# 1a. Create the trust policy document
cat > /tmp/trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::373447294617:root" },
      "Action": "sts:AssumeRole",
      "Comment": "Allows admin users/scripts to assume this role for manual testing"
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Comment": "Lambda needs to assume this role to execute the invoke-agentcore function"
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Comment": "Bedrock assumes this role for cross-service calls during AgentCore operations"
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Comment": "AgentCore Runtime containers run as this role (Bedrock, Secrets, ECR access)"
    }
  ]
}
EOF

# 1b. Create the role
aws iam create-role \
  --role-name Bedrock_Role \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --description "Shared execution role for Lambda, AgentCore Runtime, and AgentCore Gateway"
```

### 1c. Attach AWS-managed policies

```bash
# Lambda execution: write CloudWatch logs
aws iam attach-role-policy \
  --role-name Bedrock_Role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# ECR: pull Docker images (AgentCore pulls from ECR on container startup)
aws iam attach-role-policy \
  --role-name Bedrock_Role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

# Bedrock: invoke LLM models (Claude Haiku used by both agents)
aws iam attach-role-policy \
  --role-name Bedrock_Role \
  --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess
```

**Why `AmazonBedrockFullAccess`?** The agents call `bedrock:InvokeModelWithResponseStream`
(Strands SDK streaming). The managed policy covers this. Scope to specific model ARNs
in production.

### 1d. Add inline policies

```bash
# --- PassBedrockRole ---
# Required when *registering* an AgentCore Runtime: the caller must prove
# they are allowed to grant Bedrock_Role to the new runtime. Without this,
# CreateAgentRuntime fails with "AccessDenied: iam:PassRole".
cat > /tmp/pass-role.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "iam:PassRole",
    "Resource": "arn:aws:iam::373447294617:role/Bedrock_Role"
  }]
}
EOF

aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name PassBedrockRole \
  --policy-document file:///tmp/pass-role.json

# --- SecretsManagerAccess ---
# Agents call load_secrets() at container startup to inject API keys.
# The gateway also needs to read the LTA DataMall API key at runtime.
# Without this, containers fail to boot with RuntimeError.
cat > /tmp/secrets-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": [
      "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp",
      "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98"
    ]
  }]
}
EOF

aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name SecretsManagerAccess \
  --policy-document file:///tmp/secrets-policy.json

# --- AgentCoreInvoke ---
# Two separate actions are checked by AgentCore: the runtime-level ARN
# (control-plane check) and the endpoint-level ARN (data-plane check).
# Both must be allowed or the invocation returns AccessDenied.
#
# This policy covers:
#   Lambda  ──► main_agent      (direct invocation from Lambda handler)
#   main_agent ──► carpark_agent  (A2A: main_agent runs as Bedrock_Role too)
cat > /tmp/agentcore-invoke.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": [
      "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM",
      "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
    ]
  }]
}
EOF

aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name AgentCoreInvoke \
  --policy-document file:///tmp/agentcore-invoke.json

# --- AgentCoreInvokeRuntime ---
# Endpoint-level check (runtime-endpoint/DEFAULT suffix).
# Also includes InvokeGateway so carpark_agent can call the AgentCore Gateway.
cat > /tmp/agentcore-invoke-runtime.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": [
      "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT",
      "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT"
    ]
  },{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeGateway",
    "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc"
  }]
}
EOF

aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name AgentCoreInvokeRuntime \
  --policy-document file:///tmp/agentcore-invoke-runtime.json

# --- AgentCoreGatewayWorkloadIdentity ---
# When the gateway makes outbound calls to LTA DataMall, it must fetch the
# stored API key from the credential vault. This uses a two-step process:
#   1. GetWorkloadAccessToken  — short-lived identity token
#   2. GetResourceApiKey       — exchange token for the actual API key
#
# The wildcard resource is required because AgentCore evaluates multiple
# ARN patterns (workload-identity-directory/* and token-vault/*) during the
# credential fetch. Scoping to one ARN causes AccessDenied on the other.
cat > /tmp/gateway-workload.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AllowGatewayWorkloadIdentityAndApiKey",
    "Effect": "Allow",
    "Action": [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetResourceApiKey"
    ],
    "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:*"
  }]
}
EOF

aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name AgentCoreGatewayWorkloadIdentity \
  --policy-document file:///tmp/gateway-workload.json
```

### Verify the role

```bash
# List all policies on Bedrock_Role
aws iam list-role-policies --role-name Bedrock_Role
aws iam list-attached-role-policies --role-name Bedrock_Role

# Check a specific inline policy
aws iam get-role-policy --role-name Bedrock_Role --policy-name AgentCoreInvoke
```

---

## Step 2 — Secrets Manager

**Why Secrets Manager instead of environment variables?**  
Environment variables on AgentCore Runtimes are visible in the AWS console and CLI
output. API keys must not appear in plaintext there. `load_secrets()` in `config.py`
fetches the secret JSON at container startup and injects each key into `os.environ`.

```bash
# 2a. Create the application secrets (OpenWeatherMap, Anthropic)
# Replace <YOUR_OWM_KEY> and <YOUR_ANTHROPIC_KEY> with real values
aws secretsmanager create-secret \
  --name "deep-agents/api-keys" \
  --description "API keys for Deep Agents weather chatbot" \
  --secret-string '{"OPENWEATHERMAP_API_KEY":"<YOUR_OWM_KEY>","ANTHROPIC_API_KEY":"<YOUR_ANTHROPIC_KEY>"}' \
  --region ap-southeast-1

# Note the SecretARN returned — you will need it for SecretsManagerAccess policy
# Example: arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp
```

The LTA DataMall API key secret is **created automatically** by the AgentCore Gateway
when you create the API key credential provider in Step 7. Do not create it manually.

```bash
# 2b. Verify the secret
aws secretsmanager get-secret-value \
  --secret-id "deep-agents/api-keys" \
  --query "SecretString" \
  --output text | python3 -m json.tool
```

---

## Step 3 — ECR (Container Registry)

**Why ECR?**  
AgentCore Runtime expects a Docker image URI from ECR. It pulls the image on first
invocation (cold start). ECR in the same region avoids cross-region data transfer fees
and latency.

```bash
# 3a. Authenticate Docker with ECR
aws ecr get-login-password --region ap-southeast-1 \
  | docker login \
    --username AWS \
    --password-stdin \
    373447294617.dkr.ecr.ap-southeast-1.amazonaws.com

# 3b. Create repositories
aws ecr create-repository --repository-name deep-agents/main-agent    --region ap-southeast-1
aws ecr create-repository --repository-name deep-agents/carpark-agent  --region ap-southeast-1
aws ecr create-repository --repository-name deep-agents/mcp-server     --region ap-southeast-1

# 3c. Build and push main_agent image
#     Build for linux/arm64 (AgentCore Runtime uses Graviton — cheaper and faster)
cd aws/agentcore/agents/main_agent

docker build --platform linux/arm64 -t deep-agents/main-agent .
docker tag  deep-agents/main-agent:latest \
            373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest

# 3d. Build and push carpark_agent image
cd ../carpark_agent

docker build --platform linux/arm64 -t deep-agents/carpark-agent .
docker tag  deep-agents/carpark-agent:latest \
            373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest

# 3e. Verify images are in ECR
aws ecr list-images --repository-name deep-agents/main-agent    --region ap-southeast-1
aws ecr list-images --repository-name deep-agents/carpark-agent --region ap-southeast-1
```

---

## Step 4 — S3 Bucket (Static Website)

**Why S3?**  
The frontend is a single-page application (`index.html`) with no server-side logic.
S3 static hosting is the simplest and cheapest way to serve it. Cognito authentication
and API calls are handled client-side.

```bash
# 4a. Create bucket (bucket names must be globally unique — adjust if needed)
aws s3api create-bucket \
  --bucket svc-agentcore \
  --region ap-southeast-1 \
  --create-bucket-configuration LocationConstraint=ap-southeast-1

# 4b. Block public access EXCEPT for the bucket policy we will add
#     (keep BlockPublicAcls=true so ACLs can't open more than the policy allows)
aws s3api put-public-access-block \
  --bucket svc-agentcore \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false

# 4c. Add bucket policy allowing anonymous GET on /public/* only
cat > /tmp/s3-bucket-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicReadPublicPrefix",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::svc-agentcore/public/*"
  }]
}
EOF

aws s3api put-bucket-policy \
  --bucket svc-agentcore \
  --policy file:///tmp/s3-bucket-policy.json

# 4d. Upload the frontend
aws s3 cp aws/s3/index.html s3://svc-agentcore/public/index.html --region ap-southeast-1

# 4e. Verify
aws s3 ls s3://svc-agentcore/public/
# The file is accessible at:
# https://svc-agentcore.s3.ap-southeast-1.amazonaws.com/public/index.html
```

**Why scope the bucket policy to `public/*`?**  
Scoping to the `public/` prefix ensures other objects in the bucket (config files,
logs, the OpenAPI schema) remain private. The browser only ever needs `index.html`
and any co-located static assets.

---

## Step 5 — Amazon Cognito (Authentication)

**Why Cognito?**  
The chatbot is a private tool — only authenticated users should be able to submit chat
messages. Cognito provides a hosted login UI with PKCE OAuth2 without building auth
infrastructure. The `oidc-client-ts` library in `index.html` handles the PKCE flow.

```bash
# 5a. Create the User Pool
aws cognito-idp create-user-pool \
  --pool-name deep-agents-users \
  --region ap-southeast-1 \
  --auto-verified-attributes email \
  --username-attributes email \
  --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":false}}'
# Note the UserPoolId returned: ap-southeast-1_9AT4o3r3W

# 5b. Create a User Pool Domain (required for the hosted login UI)
aws cognito-idp create-user-pool-domain \
  --domain deep-agents-auth \
  --user-pool-id ap-southeast-1_9AT4o3r3W \
  --region ap-southeast-1
# Hosted UI URL: https://deep-agents-auth.auth.ap-southeast-1.amazoncognito.com

# 5c. Create the App Client (PKCE — public client, no client secret)
aws cognito-idp create-user-pool-client \
  --user-pool-id ap-southeast-1_9AT4o3r3W \
  --client-name weather-chatbot-spa \
  --no-generate-secret \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes openid email phone \
  --allowed-o-auth-flows-user-pool-client \
  --callback-urls '["https://svc-agentcore.s3.ap-southeast-1.amazonaws.com/public/index.html"]' \
  --logout-urls  '["https://svc-agentcore.s3.ap-southeast-1.amazonaws.com/public/index.html"]' \
  --supported-identity-providers COGNITO \
  --region ap-southeast-1
# Note the ClientId returned: 3fc64mf1pvufqoamndct9eqqfl

# 5d. Create a test user
aws cognito-idp admin-create-user \
  --user-pool-id ap-southeast-1_9AT4o3r3W \
  --username timlow84@gmail.com \
  --user-attributes Name=email,Value=timlow84@gmail.com Name=email_verified,Value=true \
  --temporary-password "Temp1234!" \
  --region ap-southeast-1

# 5e. Verify pool + client
aws cognito-idp describe-user-pool-client \
  --user-pool-id ap-southeast-1_9AT4o3r3W \
  --client-id 3fc64mf1pvufqoamndct9eqqfl \
  --region ap-southeast-1
```

**How Cognito connects to the frontend:**  
`index.html` uses `oidc-client-ts` with the UserPool domain as the `authority` and
the App Client ID as `client_id`. On login click, the browser redirects to the Cognito
Hosted UI. After authentication, Cognito redirects back to the S3 page with an
authorization code; `oidc-client-ts` exchanges it for tokens. The ID token is used
client-side to gate the chat input — it is **not** currently forwarded to API Gateway.

---

## Step 6 — Lambda Function (`invoke-agentcore`)

**Why Lambda?**  
The browser can't call AgentCore Runtime directly (it requires AWS SigV4 signing with
IAM credentials, which must not be exposed to the browser). Lambda acts as a
server-side proxy: it receives the plain JSON chat message and calls AgentCore with
properly-signed requests using its `Bedrock_Role` execution role.

```bash
# 6a. Package the handler
cd aws/lambda/invoke_agentcore
zip handler.zip handler.py

# 6b. Create the Lambda function
aws lambda create-function \
  --function-name invoke-agentcore \
  --runtime python3.12 \
  --role arn:aws:iam::373447294617:role/Bedrock_Role \
  --handler handler.lambda_handler \
  --zip-file fileb://handler.zip \
  --timeout 300 \
  --memory-size 256 \
  --environment "Variables={AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM}" \
  --region ap-southeast-1

# 6c. Wait for Active state
aws lambda get-function \
  --function-name invoke-agentcore \
  --query "Configuration.State" \
  --region ap-southeast-1

# --- Redeploy after code changes ---
zip handler.zip handler.py
aws lambda update-function-code \
  --function-name invoke-agentcore \
  --zip-file fileb://handler.zip \
  --region ap-southeast-1
```

**Key implementation detail — `runtimeSessionId` length:**  
`invoke_agent_runtime` requires `runtimeSessionId` to be at least 33 characters.
The user's `session_id` (from the browser) can be short (e.g. `"browser-test-001"`).
Always use `context.aws_request_id` (a 36-character UUID) as `runtimeSessionId`:

```python
runtime_session_id = context.aws_request_id          # always >= 33 chars ✓
agentcore_payload = json.dumps({
    "inputText": message,
    "sessionId": session_id,                          # user session (any length)
}).encode()
response = client.invoke_agent_runtime(
    agentRuntimeArn=_AGENTCORE_RUNTIME_ARN,
    payload=agentcore_payload,
    runtimeSessionId=runtime_session_id,
)
```

---

## Step 7 — AgentCore Gateway (MCP Frontend for LTA DataMall)

The gateway is the most complex component. It consists of:
1. **Gateway** — the MCP endpoint with IAM auth
2. **API Key Credential Provider** — stores the LTA DataMall AccountKey
3. **Gateway Target** — links the OpenAPI schema and credential provider
4. **Lambda Interceptor** — optional MCP-passthrough Lambda (does NOT rename headers)

**Why a Gateway instead of calling LTA DataMall directly from the agent?**  
The gateway converts the REST API into an MCP tool (`get_nearby_carparks`) that any
MCP-capable agent can discover and call. The agent doesn't need to know the LTA
DataMall API contract — it just uses the tool with `lat`, `lon`, `limit` parameters.

### 7a. Upload the OpenAPI schema to S3

The gateway reads the schema from S3 to understand what the backend REST API looks
like and to auto-generate the MCP tool definition.

```bash
# The schema describes the LTA DataMall endpoint, parameters, and response shape.
# IMPORTANT: Do NOT include parameters with $ in their names (e.g. $skip, $top).
# Claude's tool input_schema rejects property keys matching [^a-zA-Z0-9_.-].
aws s3 cp aws/agentcore/gateway/sg_carpark_openapi.yaml \
  s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml \
  --region ap-southeast-1
```

### 7b. Create the Gateway

```bash
aws bedrock-agentcore-control create-gateway \
  --cli-input-json file://aws/agentcore/gateway/create_gateway_input.json \
  --region ap-southeast-1

# create_gateway_input.json contents:
# {
#   "name": "sg-carpark-gateway",
#   "description": "MCP gateway fronting the LTA DataMall Carpark Availability REST API with IAM inbound auth",
#   "roleArn": "arn:aws:iam::373447294617:role/Bedrock_Role",
#   "protocolType": "MCP",
#   "protocolConfiguration": {
#     "mcp": {
#       "instructions": "Use this gateway to find real-time Singapore carpark lot availability...",
#       "supportedVersions": ["2025-06-18", "2025-03-26"],
#       "streamingConfiguration": { "enableResponseStreaming": false }
#     }
#   },
#   "authorizerType": "AWS_IAM",
#   "exceptionLevel": "DEBUG"
# }

# Note the gatewayId returned: sg-carpark-gateway-z3fc8wewlc
# MCP endpoint: https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp

# Check gateway status
aws bedrock-agentcore-control list-gateways --region ap-southeast-1 --output json
```

**Why `AWS_IAM` authorizer?**  
Any caller must sign MCP requests with SigV4 (service `bedrock-agentcore`). This
prevents open access to the LTA DataMall API key stored in the credential vault.
The carpark_agent signs requests using its `Bedrock_Role` credentials from the
container's IMDS endpoint.

### 7c. Create the API Key Credential Provider

The credential provider stores the LTA DataMall API key securely in an
AgentCore-managed secret. It is referenced by the gateway target.

```bash
# This creates a new AgentCore-managed Secrets Manager secret automatically.
# The secret name follows the pattern:
# bedrock-agentcore-identity!default/apikey/<name>-<uuid>
aws bedrock-agentcore-control create-api-key-credential-provider \
  --name lta-datamall-api-key \
  --api-key "<YOUR_LTA_DATAMALL_ACCOUNT_KEY>" \
  --region ap-southeast-1

# The command returns:
# {
#   "credentialProviderArn": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:token-vault/default/apikeycredentialprovider/lta-datamall-api-key",
#   "apiKeySecretArn": {
#     "secretArn": "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98-LMwdhV"
#   }
# }

# IMPORTANT: After creating, add the returned secretArn to SecretsManagerAccess policy (Step 1d).
# The gateway needs secretsmanager:GetSecretValue on this ARN to read the API key at runtime.
aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name SecretsManagerAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp",
        "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98"
      ]
    }]
  }'

# Verify the provider was created
aws bedrock-agentcore-control get-api-key-credential-provider \
  --name lta-datamall-api-key \
  --region ap-southeast-1
```

### 7d. Create the Gateway Target

The target links the OpenAPI schema (describing the LTA DataMall REST API) with the
credential provider (the API key). The gateway reads the schema to generate the MCP
tool and injects the API key into outbound requests.

```bash
aws bedrock-agentcore-control create-gateway-target \
  --gateway-identifier sg-carpark-gateway-z3fc8wewlc \
  --cli-input-json file://aws/agentcore/gateway/create_gateway_target_input.json \
  --region ap-southeast-1

# create_gateway_target_input.json contents:
# {
#   "gatewayIdentifier": "sg-carpark-gateway-z3fc8wewlc",
#   "name": "lta-carpark-rest-api",
#   "targetConfiguration": {
#     "mcp": {
#       "openApiSchema": {
#         "s3": {
#           "uri": "s3://bedrock-agentcore-runtime-...-naea56xtb/OpenAPI/sg_carpark_openapi.yaml",
#           "bucketOwnerAccountId": "373447294617"
#         }
#       }
#     }
#   },
#   "credentialProviderConfigurations": [{
#     "credentialProviderType": "API_KEY",
#     "credentialProvider": {
#       "apiKeyCredentialProvider": {
#         "providerArn": "arn:aws:bedrock-agentcore:...:token-vault/default/apikeycredentialprovider/lta-datamall-api-key",
#         "credentialParameterName": "AccountKey",    <-- CRITICAL: must be "AccountKey"
#         "credentialLocation": "HEADER"              <-- inject as HTTP header, not query param
#       }
#     }
#   }]
# }

# Verify the target was created and is READY
aws bedrock-agentcore-control get-gateway-target \
  --gateway-identifier sg-carpark-gateway-z3fc8wewlc \
  --target-id D6NDMNRBLS \
  --region ap-southeast-1
```

**Why `credentialParameterName: "AccountKey"` matters critically:**  
LTA DataMall's `CarParkAvailabilityv2` endpoint returns `404 "The requested API was
not found"` for ANY request that lacks a valid `AccountKey` header — including requests
with `x-api-key` or `Authorization` headers. This is their convention for
unauthenticated access. The `credentialParameterName` field tells the gateway the exact
HTTP header name to use. Without it, the default (`x-api-key`) is used and every
`tools/call` returns 404.

**Why NOT rename in the Lambda interceptor?**  
The Lambda interceptor at the `REQUEST` interception point only receives MCP protocol
messages (`event["mcp"]`). It does NOT receive the outgoing HTTP target requests
(`event["http"]`) — those never appear in the Lambda logs regardless of interceptor
configuration. Header renaming in the interceptor is therefore dead code for the
outbound path.

### 7e. Deploy the Lambda Interceptor

The interceptor is an optional but useful component. Currently it only passes MCP
messages through unchanged (logging them). The header rename is handled by the
credential provider (Step 7d), not the interceptor.

```bash
# 7e-i. Create the interceptor Lambda
cd aws/agentcore/gateway
zip lta_datamall_api_interceptor.zip lta_datamall_api_interceptor.py

aws lambda create-function \
  --function-name lta-datamall-api-interceptor \
  --runtime python3.12 \
  --role arn:aws:iam::373447294617:role/Bedrock_Role \
  --handler lta_datamall_api_interceptor.handler \
  --zip-file fileb://lta_datamall_api_interceptor.zip \
  --timeout 30 \
  --memory-size 128 \
  --region ap-southeast-1

# 7e-ii. Grant the AgentCore Gateway permission to invoke the interceptor Lambda.
#        The SourceArn condition locks it to only this gateway.
aws lambda add-permission \
  --function-name lta-datamall-api-interceptor \
  --statement-id AllowBedrockAgentCoreGatewayInvoke \
  --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com \
  --source-arn arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc \
  --region ap-southeast-1

# 7e-iii. Register the interceptor on the gateway
aws bedrock-agentcore-control update-gateway \
  --cli-input-json file://aws/agentcore/gateway/update_gateway_interceptor_input.json \
  --region ap-southeast-1

# update_gateway_interceptor_input.json contents:
# {
#   "gatewayIdentifier": "sg-carpark-gateway-z3fc8wewlc",
#   "name": "sg-carpark-gateway",
#   ...(all gateway fields)...
#   "interceptorConfigurations": [{
#     "interceptor": {
#       "lambda": { "arn": "arn:aws:lambda:ap-southeast-1:373447294617:function:lta-datamall-api-interceptor" }
#     },
#     "interceptionPoints": ["REQUEST"],
#     "inputConfiguration": { "passRequestHeaders": true }
#   }]
# }

# Redeploy interceptor after code changes
zip lta_datamall_api_interceptor.zip lta_datamall_api_interceptor.py
aws lambda update-function-code \
  --function-name lta-datamall-api-interceptor \
  --zip-file fileb://lta_datamall_api_interceptor.zip \
  --region ap-southeast-1
```

**Critical interceptor response format:**  
The gateway rejects responses that use wrong key names. Always use:

```python
# CORRECT ✓
{
  "interceptorOutputVersion": "1.0",       # NOT "interceptorInputVersion"
  "mcp": {
    "transformedGatewayRequest": {          # NOT "gatewayRequest"
      "body": body
    }
  }
}

# WRONG ✗ — causes "Received invalid response from interceptor"
{
  "interceptorInputVersion": "1.0",        # wrong key
  "mcp": {
    "gatewayRequest": { "body": body }     # wrong key
  }
}
```

### 7f. Test the gateway

```bash
# Install test dependencies
uv add httpx botocore

# Run the full MCP session test (initialize → tools/list → tools/call)
uv run python test_gateway.py

# Expected: tools/call returns real carpark data
# {"CarParkID":"2","Area":"Marina","Development":"Marina Square","AvailableLots":1618,...}
```

---

## Step 8 — AgentCore Runtime: `main_agent`

**Why AgentCore Runtime instead of Lambda?**  
The main_agent is a stateful Strands agent that may need to maintain conversation
context across multiple LLM turns within a single user request. AgentCore Runtime
provides container-based execution with warm instances, scaling, and built-in
health checking — more suitable than Lambda for multi-turn LLM workloads.

```bash
# 8a. Write the artifact config (the Docker image URI)
cat > /tmp/artifact.json << 'EOF'
{
  "containerConfiguration": {
    "containerUri": "373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest"
  }
}
EOF

cat > /tmp/netconfig.json << 'EOF'
{"networkMode": "PUBLIC"}
EOF

# 8b. Create the main_agent runtime
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name deepAgentsMainAgent \
  --agent-runtime-artifact file:///tmp/artifact.json \
  --role-arn arn:aws:iam::373447294617:role/Bedrock_Role \
  --network-configuration file:///tmp/netconfig.json \
  --environment-variables '{
    "AWS_REGION": "ap-southeast-1",
    "CLAUDE_MODEL": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "SECRET_NAME": "deep-agents/api-keys",
    "CARPARK_AGENT_ARN": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
  }' \
  --region ap-southeast-1

# Note the agentRuntimeId returned: deepAgentsMainAgent-mjSHXIEuzM

# 8c. Wait until READY
aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM \
  --query "status" \
  --region ap-southeast-1

# 8d. Update after a code change (build new image, then update runtime)
docker build --platform linux/arm64 -t deep-agents/main-agent .
docker tag  deep-agents/main-agent:latest \
            373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest

aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM \
  --agent-runtime-artifact file:///tmp/artifact.json \
  --role-arn arn:aws:iam::373447294617:role/Bedrock_Role \
  --network-configuration file:///tmp/netconfig.json \
  --region ap-southeast-1
```

**Environment variables — why each is needed:**

| Variable | Value | Reason |
|---|---|---|
| `AWS_REGION` | `ap-southeast-1` | Passed explicitly so `boto3.client(region_name=...)` doesn't need to discover it. |
| `CLAUDE_MODEL` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model ID for the Strands `BedrockModel`. Uses the `global.` prefix for cross-region inference (lower latency, higher quota). |
| `SECRET_NAME` | `deep-agents/api-keys` | Tells `load_secrets()` which secret to fetch. API keys are not in environment variables directly. |
| `CARPARK_AGENT_ARN` | `arn:aws:bedrock-agentcore:...:runtime/deepAgentsCarparkAgent-...` | The main_agent's A2A tool `get_nearby_carparks_via_agent` reads this to know which runtime to invoke. If not set, the tool returns an error and no carpark data is returned. |

---

## Step 9 — AgentCore Runtime: `carpark_agent`

The carpark_agent is a separate Strands agent that:
1. Connects to the AgentCore Gateway via SigV4-signed MCP
2. Discovers the `get_nearby_carparks` tool at runtime
3. Uses Claude to call the tool with the provided coordinates
4. Returns structured JSON back to main_agent via A2A

```bash
# 9a. Build and push carpark_agent image (if not already done in Step 3)
cd aws/agentcore/agents/carpark_agent

docker build --platform linux/arm64 -t deep-agents/carpark-agent .
docker tag  deep-agents/carpark-agent:latest \
            373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest

# 9b. Create the carpark_agent runtime
cat > /tmp/carpark-artifact.json << 'EOF'
{
  "containerConfiguration": {
    "containerUri": "373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest"
  }
}
EOF

aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name deepAgentsCarparkAgent \
  --agent-runtime-artifact file:///tmp/carpark-artifact.json \
  --role-arn arn:aws:iam::373447294617:role/Bedrock_Role \
  --network-configuration file:///tmp/netconfig.json \
  --environment-variables '{
    "AWS_REGION": "ap-southeast-1",
    "CLAUDE_MODEL": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "SECRET_NAME": "deep-agents/api-keys",
    "GATEWAY_MCP_URL": "https://sg-carpark-gateway-z3fc8wewlc.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp"
  }' \
  --region ap-southeast-1

# Note the agentRuntimeId returned: deepAgentsCarparkAgent-t3soUG2aAd

# 9c. Wait until READY
aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id deepAgentsCarparkAgent-t3soUG2aAd \
  --query "status" \
  --region ap-southeast-1

# 9d. Update main_agent with the carpark_agent ARN (if you created carpark AFTER main)
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM \
  --agent-runtime-artifact file:///tmp/artifact.json \
  --role-arn arn:aws:iam::373447294617:role/Bedrock_Role \
  --network-configuration file:///tmp/netconfig.json \
  --environment-variables '{
    "AWS_REGION": "ap-southeast-1",
    "CLAUDE_MODEL": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "SECRET_NAME": "deep-agents/api-keys",
    "CARPARK_AGENT_ARN": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
  }' \
  --region ap-southeast-1
```

---

## Step 10 — API Gateway + Lambda Integration

**Why API Gateway (HTTP API)?**  
The browser can call API Gateway over plain HTTPS without AWS credentials. API Gateway
handles TLS termination, CORS, throttling, and routes requests to the Lambda backend.

```bash
# 10a. Create the HTTP API
aws apigatewayv2 create-api \
  --name deep-agents-chat \
  --protocol-type HTTP \
  --cors-configuration \
    AllowOrigins='["*"]',AllowMethods='["POST","OPTIONS"]',AllowHeaders='["Content-Type"]' \
  --region ap-southeast-1
# Note the ApiId returned: p388lb687k

# 10b. Create the Lambda integration
aws apigatewayv2 create-integration \
  --api-id p388lb687k \
  --integration-type AWS_PROXY \
  --integration-uri arn:aws:lambda:ap-southeast-1:373447294617:function:invoke-agentcore \
  --payload-format-version 2.0 \
  --region ap-southeast-1
# Note the IntegrationId returned

# 10c. Create the POST /chat route
aws apigatewayv2 create-route \
  --api-id p388lb687k \
  --route-key "POST /chat" \
  --target "integrations/<IntegrationId>" \
  --region ap-southeast-1

# 10d. Create the default stage (auto-deploy)
aws apigatewayv2 create-stage \
  --api-id p388lb687k \
  --stage-name '$default' \
  --auto-deploy \
  --region ap-southeast-1

# 10e. Grant API Gateway permission to invoke the Lambda
#      (API Gateway uses its own service principal, not Bedrock_Role)
aws lambda add-permission \
  --function-name invoke-agentcore \
  --statement-id AllowAPIGatewayInvoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:ap-southeast-1:373447294617:p388lb687k/*" \
  --region ap-southeast-1

# 10f. Test the endpoint
curl -s -X POST \
  https://p388lb687k.execute-api.ap-southeast-1.amazonaws.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What is the weather in Singapore?","session_id":"test-001"}' \
  | python3 -m json.tool
```

**Why `lambda:InvokeFunction` on a resource policy instead of on `Bedrock_Role`?**  
API Gateway invokes Lambda using its own service principal (`apigateway.amazonaws.com`),
not as `Bedrock_Role`. IAM identity policies on a role only apply to *who is assumed
the role*. To allow a different service principal to call your Lambda, you attach a
**resource-based policy** to the Lambda function itself — this is what
`aws lambda add-permission` does.

---

## Step 11 — Update IAM Policies with Final ARNs

After creating all runtimes, update the IAM policies to include the actual ARNs:

```bash
# Update AgentCoreInvoke with both runtime ARNs
aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name AgentCoreInvoke \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": [
        "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM",
        "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
      ]
    }]
  }'

# Update AgentCoreInvokeRuntime with both runtime-endpoint ARNs + gateway InvokeGateway
aws iam put-role-policy \
  --role-name Bedrock_Role \
  --policy-name AgentCoreInvokeRuntime \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": [
        "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT",
        "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT"
      ]
    },{
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeGateway",
      "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc"
    }]
  }'
```

---

## Step 12 — End-to-End Verification

```bash
# 12a. Test the gateway directly (Python)
uv run python test_gateway.py
# Expected: tools/call returns real carpark data with isError: false

# 12b. Test the carpark_agent runtime (Python)
python3 - << 'EOF'
import boto3, json, uuid
ac = boto3.client("bedrock-agentcore", region_name="ap-southeast-1")
resp = ac.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd",
    payload=json.dumps({"lat": 1.3048, "lon": 103.8318, "limit": 3}).encode(),
    runtimeSessionId="test-carpark-" + uuid.uuid4().hex,
)
print(json.loads(resp["response"].read()))
EOF
# Expected: {"output": "...", "carparks": [...3 carparks...], "sessionId": ""}

# 12c. Test the full browser chain
curl -s -X POST \
  https://p388lb687k.execute-api.ap-southeast-1.amazonaws.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Find 3 carparks near lat=1.3048, lon=103.8318","session_id":"test-browser-001"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['text'][:200]); print(f\"Carparks: {len(d.get('carparks') or [])}\")";

# 12d. Check Lambda logs for errors
aws logs tail /aws/lambda/invoke-agentcore --since 10m --region ap-southeast-1

# 12e. Check carpark_agent runtime logs
aws logs tail /aws/bedrock-agentcore/runtimes/deepAgentsCarparkAgent-t3soUG2aAd-DEFAULT \
  --since 10m --region ap-southeast-1

# 12f. Check interceptor Lambda logs
aws logs tail /aws/lambda/lta-datamall-api-interceptor --since 10m --region ap-southeast-1
```

---

## IAM Permissions — Conceptual Guide

### The Two Types of IAM Policy

| Type | Attached to | Controls | Example use in this stack |
|---|---|---|---|
| **Identity policy** (role/user policy) | IAM role or user | What the *caller* is allowed to do | `Bedrock_Role` can call `InvokeAgentRuntime` |
| **Resource policy** | AWS resource (Lambda, S3, etc.) | Who is allowed to access the *resource* | Lambda allows `apigateway.amazonaws.com` to invoke it |

Both must allow the action for it to succeed. Either one denying it causes `AccessDenied`.

### Why `Bedrock_Role` Needs Both Runtime and Endpoint ARNs

```
Lambda calls InvokeAgentRuntime
    │
    ▼
AgentCore control plane checks:
    ① Is caller allowed on the runtime ARN?
       arn:.../runtime/deepAgentsMainAgent-mjSHXIEuzM
       → AgentCoreInvoke policy ✓
    │
    ▼
AgentCore data plane checks:
    ② Is caller allowed on the endpoint ARN?
       arn:.../runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT
       → AgentCoreInvokeRuntime policy ✓
    │
    ▼
Request reaches the container ✓
```

Omitting either check results in `AccessDenied` — the error message may not specify
which level failed.

### Why the Gateway Workload Identity Needs a Wildcard Resource

```
tools/call arrives at gateway
    │
    ▼ gateway needs to fetch the API key
    AgentCore checks:
    ① GetWorkloadAccessToken on workload-identity-directory/* ARN
    ② GetResourceApiKey on token-vault/* ARN
    │
    ▼
    Both ARN patterns must be allowed.
    Scoping to one causes AccessDenied on the other.
    Solution: Resource: "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:*"
```

### How the `AccountKey` Header Gets Injected

```
carpark_agent calls tools/call via MCP
    │
    ▼
AgentCore Gateway receives the request
    │ reads credentialParameterName = "AccountKey"
    │ reads credentialLocation      = "HEADER"
    │ fetches API key from credential vault (Step 7c)
    │
    ▼
Gateway sends outbound HTTP to LTA DataMall:
    GET /ltaodataservice/CarParkAvailabilityv2
    AccountKey: <your-lta-datamall-api-key>
    │
    ▼
LTA DataMall responds 200 with carpark data ✓
```

---

## Quick Reference — Common Maintenance Commands

```bash
# Redeploy Lambda handler
cd aws/lambda/invoke_agentcore && zip handler.zip handler.py
aws lambda update-function-code --function-name invoke-agentcore \
  --zip-file fileb://handler.zip --region ap-southeast-1

# Redeploy interceptor Lambda
cd aws/agentcore/gateway && zip lta_datamall_api_interceptor.zip lta_datamall_api_interceptor.py
aws lambda update-function-code --function-name lta-datamall-api-interceptor \
  --zip-file fileb://lta_datamall_api_interceptor.zip --region ap-southeast-1

# Update OpenAPI schema on gateway
aws s3 cp aws/agentcore/gateway/sg_carpark_openapi.yaml \
  s3://bedrock-agentcore-runtime-373447294617-ap-southeast-1-naea56xtb/OpenAPI/sg_carpark_openapi.yaml
aws bedrock-agentcore-control update-gateway-target \
  --gateway-identifier sg-carpark-gateway-z3fc8wewlc --target-id D6NDMNRBLS \
  --cli-input-json file://aws/agentcore/gateway/create_gateway_target_input.json \
  --region ap-southeast-1

# Update main_agent image
cd aws/agentcore/agents/main_agent
docker build --platform linux/arm64 -t deep-agents/main-agent . && \
docker tag deep-agents/main-agent:latest \
  373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest && \
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM \
  --agent-runtime-artifact file:///tmp/artifact.json \
  --role-arn arn:aws:iam::373447294617:role/Bedrock_Role \
  --network-configuration file:///tmp/netconfig.json --region ap-southeast-1

# Update carpark_agent image
cd aws/agentcore/agents/carpark_agent
docker build --platform linux/arm64 -t deep-agents/carpark-agent . && \
docker tag deep-agents/carpark-agent:latest \
  373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest && \
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/carpark-agent:latest
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id deepAgentsCarparkAgent-t3soUG2aAd \
  --agent-runtime-artifact file:///tmp/carpark-artifact.json \
  --role-arn arn:aws:iam::373447294617:role/Bedrock_Role \
  --network-configuration file:///tmp/netconfig.json --region ap-southeast-1

# Check status of all runtimes
aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM \
  --query "{name:agentRuntimeName,status:status,version:agentRuntimeVersion}" \
  --region ap-southeast-1

aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id deepAgentsCarparkAgent-t3soUG2aAd \
  --query "{name:agentRuntimeName,status:status,version:agentRuntimeVersion}" \
  --region ap-southeast-1

# Check gateway status
aws bedrock-agentcore-control list-gateways --region ap-southeast-1 --output table
```

---

*Generated by Claude Code — 2026-05-27.*  
*Based on the live account `373447294617`, region `ap-southeast-1`.*  
*Full stack verified end-to-end on 2026-05-27.*
