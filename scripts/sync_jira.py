"""Sync Maestro user-story status from docs/maestro-user-stories.csv to Jira.

Why: The CSV is the local source of truth for feature stories. This script keeps
Jira tickets aligned with the CSV and adds a progress comment when statuses
change or on explicit request.
What: Reads the CSV, fetches MSTRO issues, maps by summary, transitions status
if needed, and posts a comment with the current Maestro version/release notes.
Test: Run with --dry-run first; it prints what it would change.

Environment:
    JIRA_BASE_URL    e.g. https://mervenai.atlassian.net
    JIRA_EMAIL       Jira user email
    JIRA_API_TOKEN   API token
    JIRA_PROJECT     project key, default MSTRO
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


def _auth_header(email: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


def _jira_get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{client.base_url}{path}"
    r = client.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _jira_post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{client.base_url}{path}"
    r = client.post(url, json=payload, timeout=60)
    r.raise_for_status()
    if not r.content:
        return {}
    return r.json()


def _fetch_all_issues(client: httpx.Client, project: str) -> dict[str, dict[str, Any]]:
    """Return a dict mapping lowercase summary to issue."""
    results: dict[str, dict[str, Any]] = {}
    payload: dict[str, Any] = {
        "jql": f"project={project}",
        "maxResults": 100,
        "fields": ["summary", "status", "issuetype", "description"],
    }
    while True:
        data = _jira_post(client, "/rest/api/3/search/jql", payload)
        for issue in data.get("issues", []):
            summary = issue["fields"]["summary"].strip().lower()
            results[summary] = issue
        if data.get("isLast", True):
            break
        payload["nextPageToken"] = data.get("nextPageToken")
        if not payload["nextPageToken"]:
            break
    return results


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _status_name(csv_status: str) -> str:
    mapping = {
        "Done": "Done",
        "In Progress": "In Progress",
        "Backlog": "Backlog",
    }
    return mapping.get(csv_status, csv_status)


def _transitions(client: httpx.Client, issue_key: str) -> dict[str, str]:
    """Return {status_name: transition_id} for an issue."""
    data = _jira_get(client, f"/rest/api/3/issue/{issue_key}/transitions")
    return {t["to"]["name"]: t["id"] for t in data.get("transitions", [])}


def _transition(client: httpx.Client, issue_key: str, transition_id: str) -> None:
    _jira_post(
        client,
        f"/rest/api/3/issue/{issue_key}/transitions",
        {"transition": {"id": transition_id}},
    )


def _add_comment(client: httpx.Client, issue_key: str, body: str) -> None:
    _jira_post(
        client,
        f"/rest/api/3/issue/{issue_key}/comment",
        {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        },
    )


def _doc_text(text: str) -> dict[str, Any]:
    """Return an Atlassian doc-format paragraph with plain text."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _create_issue(
    client: httpx.Client,
    project: str,
    summary: str,
    description: str,
    acceptance: str,
    labels: list[str],
) -> dict[str, Any]:
    """Create a Story issue in Jira and return the created issue."""
    body_text = f"{description}\n\nAcceptance Criteria:\n{acceptance}"
    payload = {
        "fields": {
            "project": {"key": project},
            "summary": summary,
            "issuetype": {"name": "Story"},
            "description": _doc_text(body_text),
            "labels": [label.strip() for label in labels if label.strip()],
        }
    }
    return _jira_post(client, "/rest/api/3/issue", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Maestro stories to Jira")
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "docs" / "maestro-user-stories.csv",
        help="Path to maestro-user-stories.csv",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without applying")
    parser.add_argument("--comment", default="", help="Optional comment to add to updated issues")
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create Jira issues for CSV stories that do not exist yet",
    )
    args = parser.parse_args()

    base_url = os.environ.get("JIRA_BASE_URL", "https://mervenai.atlassian.net").rstrip("/")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    project = os.environ.get("JIRA_PROJECT", "MSTRO")

    if not email or not token:
        print("Set JIRA_EMAIL and JIRA_API_TOKEN", file=sys.stderr)
        return 1

    headers = {
        "Authorization": _auth_header(email, token),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    stories = _load_csv(args.csv)
    with httpx.Client(base_url=base_url, headers=headers) as client:
        issues = _fetch_all_issues(client, project)

        changed = 0
        created = 0
        not_found: list[str] = []
        for story in stories:
            summary = story["Summary"].strip().lower()
            target_status = _status_name(story["Status"])
            issue = issues.get(summary)
            if issue is None:
                not_found.append(story["Story ID"])
                if args.create_missing and not args.dry_run:
                    print(f"Creating {story['Story ID']}: {story['Summary']}")
                    new_issue = _create_issue(
                        client,
                        project,
                        story["Summary"],
                        story["Description"],
                        story["Acceptance Criteria"],
                        story["Labels"].split(","),
                    )
                    key = new_issue.get("key")
                    if key:
                        created += 1
                        issues[summary] = {
                            "key": key,
                            "fields": {"status": {"name": "Backlog"}},
                        }
                        issue = issues[summary]
                if issue is None:
                    continue

            current_status = issue["fields"]["status"]["name"]
            if current_status == target_status:
                continue

            key = issue["key"]
            print(f"{key}: {current_status} -> {target_status} ({story['Story ID']})")
            if args.dry_run:
                continue

            transitions = _transitions(client, key)
            transition_id = transitions.get(target_status)
            if transition_id is None:
                print(
                    f"  Warning: no transition to '{target_status}' available; skipping",
                    file=sys.stderr,
                )
                continue

            _transition(client, key, transition_id)
            changed += 1

            comment = args.comment or (
                f"Status synced from maestro-user-stories.csv to {target_status}. "
                f"Story: {story['Story ID']}"
            )
            _add_comment(client, key, comment)

    print(f"\nUpdated {changed} issue(s), created {created} issue(s).")
    if not_found and not args.create_missing:
        print(f"Not found in Jira ({len(not_found)}): {', '.join(not_found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
