"""Bulk-delete a JSON list of page_ids from Pinkas, via the plain page API
(app/routers/pages.py) rather than any admin/maintenance tooling.

Expected input JSON — a list of page_id strings:

  [
    "b1e2...",
    "9f3a...",
    ...
  ]

Deletes run as a dedicated no-workflow service user, auto-provisioned on first run
(shared with scripts/bulk_upload_terms.py), so deletions apply immediately instead
of queuing for approval (see app/services/mutations.py: apply_page_mutation routes
through a workflow only if the acting user has one). Re-running against the same
file is safe — page_ids already deleted or unknown (404) are reported as skipped,
not failed.

Usage:
  ADMIN_USER_ID=admin1 BASE_URL=http://localhost:8080 python scripts/bulk_delete_terms.py page_ids.json
"""

import json
import os
import sys

import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "admin1")  # used once, to provision the import user
IMPORT_USER_ID = os.environ.get("IMPORT_USER_ID")  # skip provisioning if already known
IMPORT_USER_NAME = "term-importer"


def _headers(user_id: str) -> dict:
    return {"X-User-Id": user_id, "Content-Type": "application/json"}


def ensure_import_user() -> str:
    """Find or create a permission_level=editor, workflow_id=None service user for imports."""
    if IMPORT_USER_ID:
        return IMPORT_USER_ID

    resp = requests.get(f"{BASE_URL}/users", headers=_headers(ADMIN_USER_ID))
    resp.raise_for_status()
    for u in resp.json():
        if u["name"] == IMPORT_USER_NAME and u["workflow_id"] is None:
            return u["user_id"]

    resp = requests.post(
        f"{BASE_URL}/users",
        headers=_headers(ADMIN_USER_ID),
        json={"name": IMPORT_USER_NAME, "permission_level": "editor"},
    )
    resp.raise_for_status()
    user = resp.json()
    print(f"Provisioned import user: {user['user_id']} ({IMPORT_USER_NAME})")
    return user["user_id"]


def load_page_ids(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        page_ids = json.load(f)
    if not isinstance(page_ids, list):
        sys.exit(f"{path} must contain a JSON list of page_id strings")

    errors = [
        f"  page_ids[{i}]: expected a non-empty string, got {pid!r}"
        for i, pid in enumerate(page_ids)
        if not isinstance(pid, str) or not pid.strip()
    ]
    if errors:
        sys.exit("Malformed page_ids in " + path + ":\n" + "\n".join(errors))

    return page_ids


def delete_term(user_id: str, page_id: str) -> str:
    resp = requests.delete(f"{BASE_URL}/pages/{page_id}", headers=_headers(user_id))
    if resp.status_code == 404:
        return "skipped"
    resp.raise_for_status()
    result = resp.json()
    if result["status"] != "deleted":
        return f"unexpected:{result['status']}"  # import user shouldn't have a workflow
    return "deleted"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/bulk_delete_terms.py <page_ids.json>")

    page_ids = load_page_ids(sys.argv[1])
    user_id = ensure_import_user()

    counts: dict[str, int] = {}
    for page_id in page_ids:
        try:
            outcome = delete_term(user_id, page_id)
        except requests.HTTPError as e:
            outcome = "failed"
            print(f"  FAILED {page_id!r}: {e.response.status_code} {e.response.text}")
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  {outcome:9s} {page_id}")

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
