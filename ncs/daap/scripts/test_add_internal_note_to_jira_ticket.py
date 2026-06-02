"""Interactive test - list open JSM incidents, select one, and add an internal note.

Run with (the -s flag is required to allow stdin/stdout interaction):
    .venv\\Scripts\\python.exe -m pytest tests/test_add_internal_note_to_jira_ticket.py -v -s
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

# JIRA_BASE_URL is stored as "JIRA_BASE_URL=https://..." in .env (double-prefixed)
_raw_base_url = os.getenv("JIRA_BASE_URL", "")
JIRA_BASE_URL = _raw_base_url.removeprefix("JIRA_BASE_URL=").rstrip("/")

JIRA_API_KEY = os.getenv("JIRA_API_KEY", "")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL", "")

OPEN_INCIDENTS_JQL = 'issuetype = "[System] Incident" AND statusCategory != Done ORDER BY created DESC'
MAX_RESULTS = 50

CF_URGENCY = "customfield_10041"
CF_IMPACT = "customfield_10004"
CF_SEVERITY = "customfield_10047"

ISSUE_FIELDS = ",".join([
    "summary", "status", "priority", "assignee", "reporter",
    "created", CF_URGENCY, CF_IMPACT, CF_SEVERITY,
])


def _auth() -> tuple[str, str]:
    return (JIRA_USER_EMAIL, JIRA_API_KEY)


def _headers(content_type: bool = False) -> dict:
    h = {"Accept": "application/json"}
    if content_type:
        h["Content-Type"] = "application/json"
    return h


def _option_value(field: dict | None) -> str:
    return (field or {}).get("value", "—")


def _fetch_open_incidents() -> list[dict]:
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    params = {"jql": OPEN_INCIDENTS_JQL, "maxResults": MAX_RESULTS, "fields": ISSUE_FIELDS}
    r = requests.get(url, auth=_auth(), headers=_headers(), params=params, timeout=15)
    assert r.status_code == 200, f"Search failed ({r.status_code}): {r.text}"
    return r.json().get("issues", [])


def _print_incidents(issues: list[dict]) -> None:
    print(f"\n{'#':<4} {'Key':<12} {'Priority':<10} {'Urgency':<12} {'Impact':<20} {'Status':<15} Summary")
    print("-" * 100)
    for idx, issue in enumerate(issues, start=1):
        f = issue["fields"]
        print(
            f"{idx:<4} {issue['key']:<12}"
            f" {(f.get('priority') or {}).get('name', '—'):<10}"
            f" {_option_value(f.get(CF_URGENCY)):<12}"
            f" {_option_value(f.get(CF_IMPACT)):<20}"
            f" {(f.get('status') or {}).get('name', '—'):<15}"
            f" {f.get('summary', '')[:50]}"
        )


def _add_internal_note(issue_key: str, note_text: str) -> dict:
    """Post an internal (agent-only) note via the JSM servicedeskapi."""
    url = f"{JIRA_BASE_URL}/rest/servicedeskapi/request/{issue_key}/comment"
    body = {"body": note_text, "public": False}
    r = requests.post(url, auth=_auth(), headers=_headers(content_type=True), json=body, timeout=15)
    assert r.status_code == 201, f"Failed to add note ({r.status_code}): {r.text}"
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def require_credentials():
    missing = [v for v, name in [(JIRA_BASE_URL, "JIRA_BASE_URL"), (JIRA_API_KEY, "JIRA_API_KEY"), (JIRA_USER_EMAIL, "JIRA_USER_EMAIL")] if not v]
    if missing:
        pytest.skip(f"Missing .env vars: {', '.join(missing)}")


def test_add_internal_note_to_jira_ticket():
    """Interactively select an open incident and add an internal note to it."""
    issues = _fetch_open_incidents()

    if not issues:
        pytest.skip("No open incidents found — create one in Jira first")

    # Display the list
    print(f"\nFound {len(issues)} open incident(s):")
    _print_incidents(issues)

    # Prompt user to pick a ticket
    print()
    while True:
        raw = input("Enter ticket number (#) or key (e.g. DAAP-3): ").strip()
        if not raw:
            continue
        # Accept a row number
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(issues):
                selected = issues[idx]
                break
            print(f"  Please enter a number between 1 and {len(issues)}.")
        else:
            # Accept a key directly
            match = next((i for i in issues if i["key"].upper() == raw.upper()), None)
            if match:
                selected = match
                break
            print(f"  '{raw}' not found in the list above. Try again.")

    key = selected["key"]
    summary = selected["fields"].get("summary", "")
    print(f"\nSelected: {key} — {summary}")

    # Prompt for note text
    print()
    note_text = input("Enter internal note text: ").strip()
    if not note_text:
        pytest.fail("Note text cannot be empty.")

    # Post the internal note
    result = _add_internal_note(key, note_text)
    comment_id = result.get("id")
    is_public = result.get("public", True)

    print(f"\nInternal note added successfully.")
    print(f"  Comment ID : {comment_id}")
    print(f"  Public     : {is_public}  (False = internal/agent-only)")
    print(f"  Body       : {result.get('body', '')[:120]}")

    assert comment_id is not None
    assert is_public is False, "Expected note to be internal (public=False)"
