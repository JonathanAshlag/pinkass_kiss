"""
Ingests a git repository: shallow-clones it into a temp dir, then runs `ingest_folder`
over the working tree, so every format the registry handles (markdown, code, docx, ...)
comes along. A repo is a *source* of files rather than a file format, so this isn't a
`Processor`.

Each node's `sources[].resource` is `git+<url>@<commit>#<path>` (credentials stripped
from the URL), pinning the content to the exact commit that was cloned. The `.git`
directory is never ingested (hidden entries are skipped by the walker). Like folder
ingestion, it never commits and re-ingesting creates a new subtree.
"""

import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from kb.ingest.folder import DEFAULT_BLOB_STORE, IngestReport, ingest_folder
from kb.ingest.processors.base import ProcessorRegistry

# Transports git may use; notably excludes `ext::`, which runs arbitrary commands.
_ALLOWED_PROTOCOLS = "file:git:http:https:ssh"


class GitError(ValueError):
    """Cloning failed (bad URL/ref, no network, ...). A ValueError, so the API maps it to 422."""


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": _ALLOWED_PROTOCOLS, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=600
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git timed out") from exc
    if result.returncode != 0:
        raise GitError(f"git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _public_url(url: str) -> str:
    """`url` without any `user:password@` part, so credentials never reach the KB."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit(parts._replace(netloc=host))


def _repo_name(url: str) -> str:
    tail = re.split(r"[/:\\]", url.rstrip("/\\"))[-1]
    return tail.removesuffix(".git") or "repo"


def ingest_git_repo(
    session: Session,
    url: str,
    *,
    ref: str | None = None,
    parent_id: uuid.UUID | None = None,
    registry: ProcessorRegistry | None = None,
    tags: list[str] | None = None,
    blob_store=DEFAULT_BLOB_STORE,
) -> IngestReport:
    """Clone `url` (anything `git clone` accepts: https/ssh URL or a local path) at `ref`
    (a branch or tag; default: the remote's HEAD) and ingest its working tree under
    `parent_id`. The root folder is titled after the repo. Raises `GitError` if the
    clone fails."""
    if url.startswith("-"):
        raise GitError("invalid repository URL")
    with tempfile.TemporaryDirectory(prefix="kb-git-") as tmp:
        dest = Path(tmp) / _repo_name(url)
        branch = ["--branch", ref] if ref else []
        _git("clone", "--depth", "1", *branch, "--", url, str(dest))
        commit = _git("rev-parse", "HEAD", cwd=dest)
        public = _public_url(url)

        def resource_for(path: Path) -> str:
            return f"git+{public}@{commit}#{path.relative_to(dest).as_posix()}"

        return ingest_folder(
            session,
            dest,
            parent_id=parent_id,
            registry=registry,
            tags=tags,
            root_title=_repo_name(url),
            resource_for=resource_for,
            blob_store=blob_store,
        )
