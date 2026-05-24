"""Runtime configuration loader for the AgentCore main-agent container.

Two-layer config strategy
─────────────────────────
Layer 1 — Plain environment variables (non-sensitive)
    Set directly on the AgentCore Runtime via the AWS console, CDK, or CLI.
    These are visible in the runtime definition, so only use them for
    non-sensitive values: URLs, region names, model IDs, feature flags.

    Examples:
        CLAUDE_MODEL   = us.anthropic.claude-haiku-4-5-20251001
        AWS_REGION     = ap-southeast-1   (auto-set by the runtime)
        MCP_SERVER_URL = http://mcp-server:8080/mcp

Layer 2 — AWS Secrets Manager (sensitive)
    Store all API keys in a single Secrets Manager secret as a JSON object.
    The runtime's IAM execution role must have secretsmanager:GetSecretValue
    on that secret's ARN.  Only the *name* of the secret is passed as a plain
    env var (SECRET_NAME), which is safe to expose.

    Secret JSON structure (create once, rotate in place):
        {
            "OPENWEATHERMAP_API_KEY": "...",
            "ANTHROPIC_API_KEY":      "..."   // only if calling Anthropic directly
        }

    At container startup, load_secrets() fetches the JSON and injects every
    key into os.environ so that all downstream os.getenv() calls work
    transparently — no other code needs to change.

IAM policy to attach to the AgentCore Runtime execution role:
    {
        "Effect": "Allow",
        "Action": "secretsmanager:GetSecretValue",
        "Resource": "arn:aws:secretsmanager:<region>:<account>:secret:<secret-name>-*"
    }

Usage (call once at the top of app.py before importing agent modules):
    from config import load_secrets
    load_secrets()
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def load_secrets(secret_name: str | None = None) -> None:
    """Fetch the Secrets Manager secret and inject its keys into os.environ.

    Args:
        secret_name: Override the secret name. Defaults to the SECRET_NAME
                     environment variable. If neither is set, this is a no-op
                     (useful for local development where env vars are set
                     directly via a .env file or shell export).
    """
    name = secret_name or os.getenv("SECRET_NAME")
    if not name:
        logger.debug(
            "SECRET_NAME not set — skipping Secrets Manager lookup. "
            "Env vars must be present in the process environment."
        )
        return

    try:
        import boto3  # imported lazily so the module loads without boto3 locally

        region = os.getenv("AWS_REGION", "ap-southeast-1")
        client = boto3.client("secretsmanager", region_name=region)

        logger.info("Loading secrets from Secrets Manager: %s (region: %s)", name, region)
        response = client.get_secret_value(SecretId=name)
        secret_dict: dict = json.loads(response["SecretString"])

        injected = []
        for key, value in secret_dict.items():
            # os.environ.setdefault: plain env vars set at the runtime level win;
            # the secret fills in anything that isn't already present.
            if os.environ.setdefault(key, str(value)) == str(value):
                injected.append(key)

        logger.info("Injected %d secret key(s) into environment: %s", len(injected), injected)

    except Exception as exc:  # noqa: BLE001
        # Raise so the container fails fast on startup rather than silently
        # serving requests with missing credentials.
        raise RuntimeError(
            f"Failed to load secrets from Secrets Manager secret '{name}': {exc}"
        ) from exc
