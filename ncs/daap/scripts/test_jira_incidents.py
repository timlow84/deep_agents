"""Integration test - queries open Jira incidents via REST API v3.

Requires JIRA_BASE_URL, JIRA_API_KEY, and JIRA_USER_EMAIL to be set (loaded from .env).
Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_jira_incidents.py -v -s
"""

import os
from pathlib import Path

import truststore

# Inject Windows certificate store so the corporate proxy CA is trusted
truststore.inject_into_ssl()

import pytest
import requests
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")

# JIRA_BASE_URL is stored as "JIRA_BASE_URL=https://..." in .env (double-prefixed).
# Strip the redundant prefix if present.
_raw_base_url = os.getenv("JIRA_BASE_URL", "")
JIRA_BASE_URL = _raw_base_url.removeprefix("JIRA_BASE_URL=").rstrip("/")

JIRA_API_KEY = os.getenv("JIRA_API_KEY", "")
# Email of the Atlassian account that owns the API key (set JIRA_USER_EMAIL in .env)
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL", "")

# JQL: open JSM incidents — issue type is "[System] Incident" (not plain "Incident")
# statusCategory != Done covers Open/In Progress without relying on the resolution field
OPEN_INCIDENTS_JQL = 'issuetype = "[System] Incident" AND statusCategory != Done ORDER BY created DESC'

# Number of results per page
MAX_RESULTS = 50

# Custom field IDs for this JSM instance
CF_URGENCY = "customfield_10041"
CF_IMPACT = "customfield_10004"
CF_SEVERITY = "customfield_10047"

ISSUE_FIELDS = ",".join([
    "summary", "status", "priority", "assignee", "reporter",
    "created", "updated", "description",
    CF_URGENCY, CF_IMPACT, CF_SEVERITY,
])


def _auth() -> tuple[str, str]:
    return (JIRA_USER_EMAIL, JIRA_API_KEY)


def _headers() -> dict:
    return {"Accept": "application/json"}


def _option_value(field: dict | None) -> str:
    """Extract the display value from a JSM single-select option field."""
    return (field or {}).get("value", "—")


def _fetch_slas(issue_key: str) -> list[dict]:
    """Return SLA entries for a JSM issue via the servicedeskapi."""
    url = f"{JIRA_BASE_URL}/rest/servicedeskapi/request/{issue_key}/sla"
    r = requests.get(url, auth=_auth(), headers=_headers(), timeout=15)
    if r.status_code != 200:
        return []
    return r.json().get("values", [])


def _adf_to_text(node: dict | None) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if not node:
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    parts = [_adf_to_text(child) for child in node.get("content", [])]
    # Separate block-level nodes with a newline
    sep = "\n" if node.get("type") in {"paragraph", "bulletList", "listItem", "heading"} else ""
    return sep.join(parts).strip()


@pytest.fixture(scope="module", autouse=True)
def require_credentials():
    if not JIRA_BASE_URL or not JIRA_API_KEY:
        pytest.skip("JIRA_BASE_URL and JIRA_API_KEY must be set in .env")


def test_jira_connection():
    """Verify the Jira instance is reachable and credentials are valid."""
    if not JIRA_USER_EMAIL:
        pytest.skip("JIRA_USER_EMAIL not set in .env — add your Atlassian account email")
    url = f"{JIRA_BASE_URL}/rest/api/3/myself"
    response = requests.get(url, auth=_auth(), headers=_headers(), timeout=15)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    print(f"\nAuthenticated as: {data.get('displayName')} ({data.get('emailAddress')})")


def test_search_open_incidents():
    """Query for open incidents and print a full detail block per issue."""
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    params = {"jql": OPEN_INCIDENTS_JQL, "maxResults": MAX_RESULTS, "fields": ISSUE_FIELDS}
    response = requests.get(url, auth=_auth(), headers=_headers(), params=params, timeout=15)
    assert response.status_code == 200, (
        f"JQL search failed ({response.status_code}): {response.text}"
    )

    data = response.json()
    total = data.get("total", 0)
    issues = data.get("issues", [])

    print(f"\nOpen incidents — total: {total}, returned: {len(issues)}")
    print("=" * 80)

    for issue in issues:
        key = issue.get("key", "")
        f = issue.get("fields", {})
        slas = _fetch_slas(key)

        print(f"Key         : {key}")
        print(f"Summary     : {f.get('summary', '')}")
        print(f"Status      : {(f.get('status') or {}).get('name', '—')}")
        print(f"Priority    : {(f.get('priority') or {}).get('name', '—')}")
        print(f"Urgency     : {_option_value(f.get(CF_URGENCY))}")
        print(f"Impact      : {_option_value(f.get(CF_IMPACT))}")
        print(f"Severity    : {_option_value(f.get(CF_SEVERITY))}")
        print(f"Reporter    : {(f.get('reporter') or {}).get('displayName', '—')}")
        print(f"Assignee    : {(f.get('assignee') or {}).get('displayName', 'Unassigned')}")
        print(f"Description : {_adf_to_text(f.get('description')) or '(no description)'}")
        if slas:
            print("SLAs        :")
            for sla in slas:
                oc = sla.get("ongoingCycle", {})
                breached = oc.get("breached", False)
                remaining = (oc.get("remainingTime") or {}).get("friendly", "N/A")
                goal = (oc.get("goalDuration") or {}).get("friendly", "N/A")
                print(f"  [{sla['name']}] goal={goal}, remaining={remaining}, breached={breached}")
        print("=" * 80)

    assert isinstance(issues, list)


def test_open_incidents_fields_present():
    """Verify expected fields are returned for each incident."""
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    params = {"jql": OPEN_INCIDENTS_JQL, "maxResults": 10, "fields": ISSUE_FIELDS}
    response = requests.get(url, auth=_auth(), headers=_headers(), params=params, timeout=15)
    assert response.status_code == 200

    issues = response.json().get("issues", [])
    for issue in issues:
        assert "key" in issue
        f = issue.get("fields", {})
        assert "summary" in f
        assert "status" in f
        assert "created" in f
        assert "reporter" in f
        assert CF_URGENCY in f
        assert CF_IMPACT in f
        assert CF_SEVERITY in f
        slas = _fetch_slas(issue["key"])
        assert isinstance(slas, list)
