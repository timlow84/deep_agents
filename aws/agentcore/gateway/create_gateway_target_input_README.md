This command creates the target behind the Agentcore Gateway

Gateway  (the front door — auth, protocol, URL)
   └── Target  (where to actually send requests — backend API + schema + credentials)


This file configures the target. Breaking down each section:

(1) gatewayIdentifier
Links this target to the specific gateway that was created first. A gateway can have multiple targets (e.g. one for carparks, one for weather), each with their own schema and credentials.

(2) targetConfiguration.mcp.openApiSchema.s3
Tells the gateway what the backend REST API looks like by pointing at the OpenAPI spec in S3. The gateway reads this schema to:

- know which HTTP paths and methods exist (GET /CarParkAvailabilityv2)
- know what parameters each operation accepts (lat, lon, radius_km, limit)
- auto-generate the MCP tool definition that agents see when they connect

bucketOwnerAccountId is a safety check — AWS rejects the request if the bucket is owned by a different account, preventing confused-deputy attacks.

(3) credentialProviderConfigurations
Tells the gateway how to authenticate outbound calls to the backend. Here it references the lta-datamall-api-key credential provider (the one that holds the LTA AccountKey). The gateway injects this as an x-api-key header on every outbound request — which is then renamed to AccountKey by the Lambda interceptor before it hits LTA DataMall.

-------------------------------
So the full chain wired up by these two files is:

create_gateway_input.json          → sets up the front door (IAM auth, MCP protocol)
create_gateway_target_input.json   → sets up the back end (OpenAPI schema, API key)
update_gateway_interceptor_input.json → patches in the header-renaming Lambda


[CLI Input to create AgentCore Gateway Target]
aws bedrock-agentcore-control create-gateway \
  --cli-input-json "file://aws/agentcore/gateway/create_gateway_input.json" \
  --region ap-southeast-1