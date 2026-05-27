1. AWS S3
# Deploy file to S3 bucket
aws s3 cp aws/s3/index.html s3://svc-agentcore/public/index.html --region ap-southeast-1

2. Main Agent
# Build arm64 docker image -> From the main_agent folder (where you already are)
docker build --platform linux/arm64 -t deep-agents/main-agent .

docker tag deep-agents/main-agent:latest 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest
docker push 373447294617.dkr.ecr.ap-southeast-1.amazonaws.com/deep-agents/main-agent:latest

# Update Main Agent with the Image
[System.IO.File]::WriteAllText("$env:TEMP\netconfig.json", '{"networkMode":"PUBLIC"}', [System.Text.UTF8Encoding]::new($false))

aws bedrock-agentcore-control update-agent-runtime --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM --agent-runtime-artifact "file://$env:TEMP\artifact.json" --role-arn "arn:aws:iam::373447294617:role/Bedrock_Role" --network-configuration "file://$env:TEMP\netconfig.json" --region ap-southeast-1

# Check Agent Status
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id deepAgentsMainAgent-mjSHXIEuzM --region ap-southeast-1 --query "status"


3. Check AgentCore Gateway
aws bedrock-agentcore-control list-gateways --region ap-southeast-1 --output json
{                                                                                                                            
    "items": [
        {
            "gatewayId": "sg-carpark-gateway-z3fc8wewlc",
            "name": "sg-carpark-gateway",
            "status": "READY",
            "description": "MCP gateway fronting the LTA DataMall Carpark Availability REST API with IAM inbound auth",
            "createdAt": "2026-05-24T12:45:57.335927+00:00",
            "updatedAt": "2026-05-25T12:46:08.661223+00:00",
            "authorizerType": "AWS_IAM",
            "protocolType": "MCP"
        }
    ]
}

4. Check IAM Role and Policy
# InvokeAgentRuntime on the carpark agent — the main agent needs this for A2A calls
# InvokeGateway on the MCP gateway — the carpark agent needs this to call the LTA gateway

aws iam get-role-policy --role-name Bedrock_Role --policy-name AgentCoreInvoke --output json       
{                                                                                                                                                                                                        
    "RoleName": "Bedrock_Role",
    "PolicyName": "AgentCoreInvoke",
    "PolicyDocument": {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM"
            },
            {
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd"
            }
        ]
    }
}

aws iam get-role-policy --role-name Bedrock_Role --policy-name AgentCoreInvokeRuntime --output json
{                                                                                                                                                                                                        
    "RoleName": "Bedrock_Role",
    "PolicyName": "AgentCoreInvokeRuntime",
    "PolicyDocument": {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsMainAgent-mjSHXIEuzM/runtime-endpoint/DEFAULT"
            },
            {
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:runtime/deepAgentsCarparkAgent-t3soUG2aAd/runtime-endpoint/DEFAULT"
            },
            {
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:gateway/sg-carpark-gateway-z3fc8wewlc"
            }
        ]
    }
}

aws iam get-role-policy --role-name Bedrock_Role --policy-name SecretsManagerAccess --output json
{
    "RoleName": "Bedrock_Role",
    "PolicyName": "SecretsManagerAccess",
    "PolicyDocument": {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:deep-agents/api-keys-Cmsypp"
            }
        ]
    }
}
{
    "RoleName": "Bedrock_Role",
    "PolicyName": "PassBedrockRole",
    "PolicyDocument": {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": "arn:aws:iam::373447294617:role/Bedrock_Role"
            }
        ]
    }
}
{
    "AttachedPolicies": [
        {
            "PolicyName": "AWSLambdaBasicExecutionRole",
            "PolicyArn": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        },
        {
            "PolicyName": "AmazonEC2ContainerRegistryReadOnly",
            "PolicyArn": "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        },
        {
            "PolicyName": "AmazonBedrockFullAccess",
            "PolicyArn": "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
        },
        {
            "PolicyName": "AmazonS3ObjectLambdaExecutionRolePolicy",
            "PolicyArn": "arn:aws:iam::aws:policy/service-role/AmazonS3ObjectLambdaExecutionRolePolicy"
        }
    ]
}


5. Bedrock AgentCore <--> AWS Secrets
aws bedrock-agentcore-control get-api-key-credential-provider --name lta-datamall-api-key --region ap-southeast-1 --output json
{
    "apiKeySecretArn": {
        "secretArn": "arn:aws:secretsmanager:ap-southeast-1:373447294617:secret:bedrock-agentcore-identity!default/apikey/lta-datamall-api-key-0e976f98-LMwdhV"
    },
    "name": "lta-datamall-api-key",
    "credentialProviderArn": "arn:aws:bedrock-agentcore:ap-southeast-1:373447294617:token-vault/default/apikeycredentialprovider/lta-datamall-api-key",
    "createdTime": "2026-05-24T22:30:20.414000+08:00",
    "lastUpdatedTime": "2026-05-24T22:30:20.414000+08:00"
}