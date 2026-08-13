"""Bulk-import a JSON list of terms into Pinkas as pages, via the plain page API
(app/routers/pages.py) rather than the agent-api or document-ingestion pipeline.

Expected input JSON — a list of objects:

  [
    {
      "title": "...",
      "description": "...",
      "body": "...",
      "tag": "...",          # must be one of GET /pages/tags
      "confidence": "...",   # free text, e.g. "high" / "low" / "0.8"
      "aliases": ["..."]     # optional, list of alternate titles
    },
    ...
  ]

`confidence` has no dedicated field on Page — it is not wired to trust_tier (every
page created here starts "unverified", same as any editor-created page). It is
folded into the page content as a leading metadata line instead.

Uploads run as a dedicated no-workflow service user, auto-provisioned on first run,
so pages publish immediately instead of queuing for approval (see app/services/
mutations.py: apply_page_mutation routes through a workflow only if the acting
user has one). Re-running against the same file is safe — pages already created
(title already taken -> 409) are reported as skipped, not failed.

Usage:
  ADMIN_USER_ID=admin1 BASE_URL=http://localhost:8080 python scripts/bulk_upload_terms.py terms.json
"""

import json
import os
import sys

import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "admin1")  # used once, to provision the import user
IMPORT_USER_ID = os.environ.get("IMPORT_USER_ID")  # skip provisioning if already known
IMPORT_USER_NAME = "term-importer"

REQUIRED_FIELDS = ("title", "description", "body", "tag")


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


def fetch_allowed_tags(user_id: str) -> set[str]:
    resp = requests.get(f"{BASE_URL}/pages/tags", headers=_headers(user_id))
    resp.raise_for_status()
    return set(resp.json()["tags"])


def load_terms(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        terms = json.load(f)
    if not isinstance(terms, list):
        sys.exit(f"{path} must contain a JSON list of term objects")

    errors = []
    for i, term in enumerate(terms):
        missing = [f for f in REQUIRED_FIELDS if not term.get(f)]
        if missing:
            errors.append(f"  term[{i}] ({term.get('title', '<no title>')!r}): missing {missing}")
    if errors:
        sys.exit("Malformed terms in " + path + ":\n" + "\n".join(errors))

    return terms


def validate_aliases(terms: list[dict]) -> None:
    errors = []
    for i, term in enumerate(terms):
        aliases = term.get("aliases")
        if aliases is None:
            continue
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            errors.append(
                f"  term[{i}] ({term.get('title', '<no title>')!r}): "
                f"aliases must be a JSON list of non-empty strings, got {aliases!r}"
            )
    if errors:
        sys.exit("Invalid aliases:\n" + "\n".join(errors))


def validate_tags(terms: list[dict], allowed_tags: set[str]) -> None:
    errors = [
        f"  term[{i}] ({term['title']!r}): tag {term['tag']!r} not in allowed tags"
        for i, term in enumerate(terms)
        if term["tag"] not in allowed_tags
    ]
    if errors:
        sys.exit(
            "Invalid tags (allowed: " + ", ".join(sorted(allowed_tags)) + "):\n"
            + "\n".join(errors)
        )


def build_payload(term: dict) -> dict:
    confidence = term.get("confidence")
    content = term["body"]
    if confidence:
        content = f"_Confidence: {confidence}_\n\n{content}"
    return {
        "title": term["title"],
        "description": term["description"],
        "content": content,
        "tags": [term["tag"]],
        "aliases": term.get("aliases") or [],
    }


def upload_term(user_id: str, term: dict) -> str:
    resp = requests.post(f"{BASE_URL}/pages", headers=_headers(user_id), json=build_payload(term))
    if resp.status_code == 409:
        return "skipped"
    resp.raise_for_status()
    result = resp.json()
    if result["status"] != "published":
        return f"unexpected:{result['status']}"  # import user shouldn't have a workflow
    return "created"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/bulk_upload_terms.py <terms.json>")

    terms = load_terms(sys.argv[1])
    validate_aliases(terms)
    user_id = ensure_import_user()
    allowed_tags = fetch_allowed_tags(user_id)
    validate_tags(terms, allowed_tags)

    counts: dict[str, int] = {}
    for term in terms:
        try:
            outcome = upload_term(user_id, term)
        except requests.HTTPError as e:
            outcome = "failed"
            print(f"  FAILED {term['title']!r}: {e.response.status_code} {e.response.text}")
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  {outcome:9s} {term['title']}")

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
