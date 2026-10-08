"""
Direct-corpus-interaction (DCI) tools: ls/grep/read-style access to the KB for agents.

Instead of a retriever, an agent explores the corpus itself with three bounded,
line-oriented primitives -- see reference/paper.md (direct corpus interaction) and
reference/project-dci-analysis.md for the reasoning:

- `list_paths`   -- `ls` / `find`: enumerate nodes as paths
- `search_lines` -- `grep -n` / `rg`: regex search, one hit per matching line
- `read_lines`   -- `read` / `sed -n`: a line-numbered slice of one file

Every tool is scoped to a manifest (the nodes `dal.resolve_manifest` expands it to)
and every output is capped at `max_chars` characters, so a single call can never flood
an agent's context. Outputs are plain text in familiar CLI shapes, ready to hand to an
agent as a tool result.

What gets searched/read is a node's *virtual file*: its frontmatter rendered as a
YAML block, followed by its markdown `content` (empty for folders, which only carry
organizational fields), so a search for a tag, alias or description hits the same way
it would in an OKF bundle on disk. The rendering and its line coordinates are owned by
`kb.okf.render_virtual_file`, the same text the semantic index numbers its chunk lines
in, so `search_lines`/`read_lines` line numbers and semantic-search hits agree.

Paths are derived at read time, not stored: `/`-joined node titles from the KB root
down. Since titles aren't unique among siblings, colliding titles get a `~<first 8
hex of id>` suffix. Every tool that takes a path also accepts a raw node uuid.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from kb.okf import render_virtual_file
from kb.storage import dal
from kb.storage.models import File, Folder, Node

DEFAULT_MAX_CHARS = 20_000
MAX_CHARS_LIMIT = 50_000
DEFAULT_READ_LIMIT = 200
_CONTENT_BATCH = 500  # files whose text search_lines holds at once


class PatternError(Exception):
    """Raised when a search pattern isn't a valid (Python `re`) regular expression."""


@dataclass
class ToolOutput:
    """A tool result: the text to show the agent, and whether it was cut at `max_chars`."""

    text: str
    truncated: bool


# --------------------------------------------------------------------------
# Scope, paths, rendering
# --------------------------------------------------------------------------


class _Scope:
    """The nodes a manifest resolves to, with their derived paths (both directions)."""

    def __init__(self, session: Session, manifest_id: uuid.UUID):
        if dal.get_manifest(session, manifest_id) is None:
            raise ValueError(f"no such manifest: {manifest_id}")
        self._session = session
        self._segments: dict[uuid.UUID, str] = {}
        self._paths: dict[uuid.UUID, str] = {}

        self.nodes = {node.id: node for node in dal.resolve_manifest(session, manifest_id)}
        for node in self.nodes.values():
            self._path_of(node)
        self.by_path = {self._paths[node_id]: node for node_id, node in self.nodes.items()}

    def path(self, node: Node) -> str:
        return self._paths[node.id]

    def lookup(self, path_or_id: str) -> Node:
        """A node in scope by path or uuid; ValueError if it isn't in this manifest."""
        key = path_or_id.strip().strip("/")
        node = self.by_path.get(key)
        if node is None:
            try:
                node = self.nodes.get(uuid.UUID(key))
            except ValueError:
                pass
        if node is None:
            raise ValueError(f"no such path in manifest: {path_or_id}{self._suggest(key)}")
        return node

    def _suggest(self, key: str, limit: int = 3) -> str:
        """` (did you mean: a | b)` for a path that doesn't resolve: paths it is a prefix of
        (the usual mistake is a shortened path, e.g. cut at a ':' in a title), else the
        deepest existing path it starts with (a guessed child of a real folder)."""
        wanted = key.lower()
        if not wanted:
            return ""
        close = sorted((p for p in self.by_path if p.lower().startswith(wanted)), key=lambda p: (len(p), p))
        if not close:
            parents = [p for p in self.by_path if wanted.startswith(p.lower() + "/")]
            close = [max(parents, key=len)] if parents else []
        return f" (did you mean: {' | '.join(close[:limit])})" if close else ""

    def _path_of(self, node: Node) -> str:
        if node.id not in self._paths:
            parent = self._session.get(Folder, node.parent_id) if node.parent_id else None
            prefix = f"{self._path_of(parent)}/" if parent is not None else ""
            self._paths[node.id] = prefix + self._segment(node)
        return self._paths[node.id]

    def _segment(self, node: Node) -> str:
        if node.id not in self._segments:
            siblings = dal.list_children(self._session, node.parent_id)
            if node not in siblings:
                siblings.append(node)
            counts: dict[str, int] = {}
            for sibling in siblings:
                base = _title_segment(sibling.title)
                counts[base] = counts.get(base, 0) + 1
            for sibling in siblings:
                base = _title_segment(sibling.title)
                self._segments[sibling.id] = (
                    f"{base}~{sibling.id.hex[:8]}" if counts[base] > 1 else base
                )
        return self._segments[node.id]


def _title_segment(title: str) -> str:
    return title.replace("/", "-").strip() or "untitled"


def _load_content(session: Session, nodes: list[Node]) -> None:
    """One query filling in the (deferred) `content` of the files among `nodes`."""
    ids = [n.id for n in nodes if isinstance(n, File)]
    if ids:
        session.scalars(select(File).where(File.id.in_(ids)).options(undefer(File.content))).all()


def _bounded(lines: list[str], max_chars: int, hint: str) -> ToolOutput:
    """Joins `lines`, cutting at a line boundary so the whole output fits `max_chars`."""
    if not 1 <= max_chars <= MAX_CHARS_LIMIT:
        raise ValueError(f"max_chars must be between 1 and {MAX_CHARS_LIMIT}")

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return ToolOutput(text, truncated=False)

    notice = f"[truncated at {max_chars} chars -- {hint}]"
    budget = max_chars - len(notice) - 1
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    if not kept and budget > 0:
        # A single overlong line: keep a prefix rather than returning nothing.
        kept.append(lines[0][:budget])
    return ToolOutput("\n".join([*kept, notice])[:max_chars], truncated=True)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def list_paths(
    session: Session,
    manifest_id: uuid.UUID,
    *,
    under: str | None = None,
    recursive: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolOutput:
    """
    `ls` over a manifest. Without `under`, lists the manifest's top-level nodes (in-scope
    nodes whose parent is out of scope); with `under` (a path or uuid), that node's
    in-scope children. `recursive=True` lists whole subtrees instead.

    One line per node: `<path>  [<status>]  <description>` for files and
    `<path>/  <description>` for folders (which have no status); description is omitted
    when unset.
    """
    scope = _Scope(session, manifest_id)

    if under is None:
        frontier = [n for n in scope.nodes.values() if n.parent_id not in scope.nodes]
    else:
        root = scope.lookup(under)
        frontier = [n for n in scope.nodes.values() if n.parent_id == root.id]

    listed: list[Node] = []
    while frontier:
        listed.extend(frontier)
        if not recursive:
            break
        ids = {n.id for n in frontier}
        frontier = [n for n in scope.nodes.values() if n.parent_id in ids]

    lines = []
    for node in sorted(listed, key=scope.path):
        if isinstance(node, Folder):
            line = scope.path(node) + "/"
        else:
            line = scope.path(node) + f"  [{node.status}]"
        if node.description:
            line += f"  {node.description}"
        lines.append(line)

    return _bounded(
        lines or ["(no entries)"],
        max_chars,
        "list a narrower subtree with `under`, or drop `recursive`",
    )


def search_lines(
    session: Session,
    manifest_id: uuid.UUID,
    patterns: str | list[str],
    *,
    paths: list[str] | None = None,
    ignore_case: bool = False,
    context: int = 0,
    files_only: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolOutput:
    """
    `grep -n` over the virtual files in a manifest (Python `re` syntax). With several
    `patterns`, a line must match *all* of them -- the equivalent of `grep a | grep b`.
    `paths` (paths or uuids) restricts the search to those nodes; a folder path covers
    its in-scope subtree.

    Output lines are `<path>:<line>:<text>` for hits and `<path>-<line>-<text>` for
    `context` lines (groups separated by `--`), or just matching paths if `files_only`.
    """
    if isinstance(patterns, str):
        patterns = [patterns]
    if not patterns:
        raise PatternError("at least one pattern is required")
    if context < 0:
        raise ValueError("context must be >= 0")
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = [re.compile(p, flags) for p in patterns]
    except re.error as exc:
        raise PatternError(f"invalid pattern: {exc}") from exc

    scope = _Scope(session, manifest_id)
    if paths is None:
        targets = list(scope.nodes.values())
    else:
        targets = []
        for p in paths:
            root = scope.lookup(p)
            prefix = scope.path(root) + "/"
            targets.append(root)
            targets.extend(n for n in scope.nodes.values() if scope.path(n).startswith(prefix))

    out: list[str] = []
    seen: set[uuid.UUID] = set()
    ordered = sorted(targets, key=scope.path)
    for i, node in enumerate(ordered):
        if i % _CONTENT_BATCH == 0:
            _load_content(session, ordered[i : i + _CONTENT_BATCH])
        if node.id in seen:
            continue
        seen.add(node.id)

        lines = render_virtual_file(node).split("\n")
        if isinstance(node, File):
            session.expire(node, ["content"])  # keep peak memory at one batch, not the manifest
        hits = [i for i, line in enumerate(lines) if all(rx.search(line) for rx in compiled)]
        if not hits:
            continue

        path = scope.path(node)
        if files_only:
            out.append(path)
            continue

        hit_set = set(hits)
        last = -1
        for i in hits:
            start, end = max(i - context, 0), min(i + context, len(lines) - 1)
            if context and last >= 0 and start > last + 1:
                out.append("--")
            for j in range(max(start, last + 1), end + 1):
                sep = ":" if j in hit_set else "-"
                out.append(f"{path}{sep}{j + 1}{sep}{lines[j]}")
            last = max(last, end)
        if context:
            out.append("--")

    if out and out[-1] == "--":
        out.pop()
    return _bounded(
        out or ["(no matches)"],
        max_chars,
        "add a pattern, restrict `paths`, or use `files_only` first",
    )


def read_lines(
    session: Session,
    manifest_id: uuid.UUID,
    path: str,
    *,
    offset: int = 1,
    limit: int = DEFAULT_READ_LIMIT,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolOutput:
    """
    `read` / `sed -n` on one virtual file in a manifest: lines `offset` (1-based) to
    `offset + limit - 1`, each prefixed with its line number and a tab, under a header
    line `<path> (lines a-b of N)`. Line numbers match `search_lines` output, so a hit
    can be expanded with `read_lines(path, offset=hit - k, limit=2k)`.
    """
    if offset < 1:
        raise ValueError("offset must be >= 1")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    scope = _Scope(session, manifest_id)
    node = scope.lookup(path)
    lines = render_virtual_file(node).split("\n")

    end = min(offset + limit - 1, len(lines))
    if offset > len(lines):
        header = f"{scope.path(node)} (offset {offset} is past the end -- {len(lines)} lines)"
        return _bounded([header], max_chars, "")

    header = f"{scope.path(node)} (lines {offset}-{end} of {len(lines)})"
    body = [f"{n}\t{lines[n - 1]}" for n in range(offset, end + 1)]
    return _bounded(
        [header, *body], max_chars, "read a smaller range with `offset`/`limit`"
    )
