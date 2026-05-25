[Why the Lambda Interceptor?]
The AgentCore API key credential provider injects the LTA key as the standard x-api-key header, but LTA DataMall expects AccountKey. This means outbound calls to LTA will return 401 until one of these is done:
- Add a Lambda interceptor on the gateway REQUEST point to rename the header, or
- Put a thin AWS API Gateway in front of LTA DataMall that translates x-api-key → AccountKey


[Implementation]
REQUEST flow (every call from MCP client → LTA DataMall):

  AgentCore Gateway injects:   x-api-key: hAIWl34nR/...
                                         │
                          ┌──────────────▼──────────────┐
                          │  lta-datamall-api-interceptor│
                          │  x-api-key  →  AccountKey   │
                          └──────────────┬──────────────┘
                                         │
  LTA DataMall receives:    AccountKey: hAIWl34nR/...  ✓

File -> aws/agentcore/gateway/lta_datamall_api_interceptor.py 
Lambda function -> arn:aws:lambda:ap-southeast-1:373447294617:function:lta-datamall-api-interceptor
Lambda resource policy -> AllowBedrockAgentCoreGatewayInvoke statement
Gateway (updated) -> sg-carpark-gateway-z3fc8wewlc


[Key properties of the Lambda]
- Case-insensitive header matching (x-api-key, X-Api-Key, etc.)
- Pass-through for all other headers unchanged
- Warning log if x-api-key is absent (so auth failures are visible in CloudWatch /aws/lambda/lta-datamall-api-interceptor)
- Scoped resource policy — only the sg-carpark-gateway can invoke it (via AWS:SourceArn condition)

To update the Lambda code after any changes:
cd aws/agentcore/gateway
# (on Windows, re-zip with PowerShell as before, then:)
aws lambda update-function-code \
  --function-name lta-datamall-api-interceptor \
  --zip-file fileb://lta_datamall_api_interceptor.zip \
  --region ap-southeast-1

lta_datamall_api_interceptor.zip
└── lta_datamall_api_interceptor.py   ← handler lives here


[Command - aws lambda update-function-code]
This command replaces the executable code inside an existing Lambda function without touching any of its configuration (runtime, role, timeout, environment variables, etc.).


[Why it exists]
Lambda stores your function's code as a deployment package in an S3 bucket managed by AWS. When you first call create-function you hand it that package. update-function-code is how you swap in a new version of that package after the fact — it's the equivalent of "re-deploy".


[Why the zip file?]
Lambda doesn't run your source file directly. It needs a self-contained deployment package — a zip archive that contains:
- your Python module(s) at the root of the zip (not nested in a subdirectory)
- any third-party libraries your code imports that aren't part of the Lambda runtime (e.g. httpx, boto3 extras)

For lta_datamall_api_interceptor.py the zip has only the one file at root level:
  lta_datamall_api_interceptor.zip
  └── lta_datamall_api_interceptor.py   ← handler lives here

Lambda's handler string lta_datamall_api_interceptor.handler means:
- module → lta_datamall_api_interceptor → finds lta_datamall_api_interceptor.py at zip root
- function → handler → calls the def handler(event, context) inside it

If the .py file were in a subfolder inside the zip, Lambda would fail with Unable to import module.


[The two ways to supply code]
Method	When to use
--zip-file fileb://...	Package is ≤ 50 MB (our interceptor is ~1.4 KB — trivially small)
--s3-bucket / --s3-key	Package is > 50 MB (large dependencies like ML libraries)
fileb:// (note the b) tells the CLI to treat the file as raw binary, not as text — important for zip files since they contain binary data.


[What happens when you run it]
1. CLI reads the zip, base64-encodes it, sends it to the Lambda API
2. Lambda replaces the code stored in its managed S3 bucket
3. The next invocation picks up the new code — no restart needed for the existing function configuration
4. AWS returns a new CodeSha256 and LastModified timestamp so you can confirm the right version landed