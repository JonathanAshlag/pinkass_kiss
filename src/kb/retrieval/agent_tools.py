"""
The KB's DCI tools (see kb.retrieval.dci) as an agent sees them: scoped to one manifest, text in,
text out. This is the agent-side adapter at the same seam the REST routes in kb.api sit
on -- what an agent framework binds as tools.

Differences from calling kb.service directly, all of which an agent loop needs:

- each call opens and closes its own session, so a tool object can outlive any one
  transaction and be called from a framework's own threads;
- a bad call (unknown path, invalid regex, out-of-range argument) comes back as an
  `error: ...` string the model can read and recover from, never as an exception that
  would abort the agent run;
- output is the tool's text only (`ToolOutput.truncated` is already spelled out in
  the text as a `[truncated ...]` notice).

`semantic_search` is a fourth, optional tool over the derived embedding index
(kb.semantic_index): it finds passages by meaning and points at them with the same paths and
line numbers `read_lines` takes. It needs the index stack (and an indexed corpus), so
`as_langchain()` only includes it when asked (`include_semantic=True`).

`as_langchain()` wraps the tools for LangChain/LangGraph; it needs `langchain-core`,
which the core `langchain` dependency installs.
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from kb import service


class AgentTools:
    def __init__(
        self, manifest_id: uuid.UUID, *, session_factory: Callable[[], Session] | None = None
    ):
        if session_factory is None:
            from kb.storage.db import SessionLocal

            session_factory = SessionLocal
        self.manifest_id = manifest_id
        self._session_factory = session_factory

    def _run(self, fn, *args, **kwargs) -> str:
        with self._session_factory() as session:
            try:
                return fn(session, self.manifest_id, *args, **kwargs).text
            except (ValueError, service.PatternError) as exc:
                return f"error: {exc}"

    def semantic_search(self, query: str, k: int = 5) -> str:
        with self._session_factory() as session:
            try:
                hits = service.semantic_search(session, self.manifest_id, query, k=k)
            except ValueError as exc:
                return f"error: {exc}"
            except Exception as exc:  # index/embeddings backend down: let the model fall back to grep
                return f"error: semantic search unavailable ({type(exc).__name__}: {exc}); use search_lines"
        if not hits:
            return "(no matches)"
        lines = []
        for h in hits:
            heading = f" [{h.heading}]" if h.heading else ""
            lines.append(f"{h.path}:{h.start_line}-{h.end_line}{heading} ({h.score:.2f})")
            lines.append(f"    {h.snippet}")
        lines.append("-- read a hit in full with read_lines(path, offset=start_line)")
        return "\n".join(lines)

    def list_paths(self, under: str | None = None, recursive: bool = False) -> str:
        # models habitually pass "." or "/" for "the root"; that means "no `under`"
        under = (under or "").strip()
        under = None if under in {"", ".", "./", "/"} else under.removeprefix("./")
        out = self._run(service.list_paths, under=under, recursive=recursive)
        if out.startswith("error: no such path"):
            out += " -- call list_paths with no arguments to see valid paths"
        return out

    def search_lines(self, pattern: str, ignore_case: bool = True, files_only: bool = False) -> str:
        return self._run(service.search_lines, pattern, ignore_case=ignore_case, files_only=files_only)

    def read_lines(self, path: str, offset: int = 1, limit: int = 50) -> str:
        return self._run(service.read_lines, path, offset=offset, limit=limit)

    def as_langchain(self, include_semantic: bool = False) -> list:
        """The tools as LangChain tools: list_paths, search_lines, read_lines, plus
        semantic_search when `include_semantic`. Their docstrings are what the model reads."""
        from langchain_core.tools import tool

        @tool
        def list_paths(under: str | None = None, recursive: bool = False) -> str:
            """List documents in the knowledge base (like `ls`/`find`).

            Call it with no arguments first to see the top level. Each line is a full path
            (folders end in "/"), then two spaces, then an optional [status] and
            description: the path is everything before those two spaces and may itself
            contain spaces and colons. Pass `under` only as a path copied exactly from a
            listing (never "." or a shortened or guessed path). `recursive=True` lists
            everything below.
            """
            return self.list_paths(under, recursive)

        @tool
        def search_lines(pattern: str, ignore_case: bool = True, files_only: bool = False) -> str:
            """Regex search over all documents (like `grep -n`). Output: path:line:text.

            Each line is matched on its own, with POSIX extended regex syntax (like
            `grep -E`): `\\y` is the word boundary (`\\b` is rejected), and
            there are no named groups or inline `(?i)`; use `ignore_case`.

            Use `files_only=True` to get just the matching paths, then `read_lines` them.
            """
            return self.search_lines(pattern, ignore_case, files_only)

        @tool
        def read_lines(path: str, offset: int = 1, limit: int = 50) -> str:
            """Read a line range of one document (like `sed -n`).

            `path` must be copied exactly from `list_paths` or `search_lines` output.
            """
            return self.read_lines(path, offset, limit)

        tools = [list_paths, search_lines, read_lines]
        if include_semantic:

            @tool
            def semantic_search(query: str, k: int = 5) -> str:
                """Search documents by meaning, not exact text. Use it for conceptual or
                paraphrased questions ("how is the model evaluated?", "what data was it
                trained on?") where you don't know the exact words the text uses; use
                search_lines instead when you know a literal string, name or number.
                Write `query` as a short natural-language description of what you are
                looking for. Output: one hit per passage as
                `path:start_line-end_line [heading] (similarity)` and a snippet line;
                read a hit with read_lines(path, offset=start_line)."""
                return self.semantic_search(query, k)

            tools.append(semantic_search)
        return tools
