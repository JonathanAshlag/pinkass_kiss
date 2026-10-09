"""Turns KB nodes into LangChain `Document`s and splits them into line-addressed chunks.

`FileNodeLoader` yields one `Document` per active file (folders have no content and
are never indexed); `split_documents`
cuts those into chunks whose metadata (`file_id`, `heading`, `start_line`, `end_line`,
`title`) matches the `kb_chunks` columns in `kb.semantic_index.vectorstore`.

Decisions:

- **What gets embedded: the markdown body, not the frontmatter.** A node's virtual file
  (`kb.okf.render_virtual_file`, the single owner of that rendering and its line
  coordinates) is a YAML frontmatter block followed by `node.content`. Only the content
  is indexed, so retagging, a status change or a description edit doesn't re-embed
  anything (frontmatter is reachable via `search_lines` already). The loader records
  how many lines precede the body in the rendered file (`line_offset`, from
  `kb.okf.body_line_offset`), and the line numbers on every chunk are **line numbers in
  the rendered virtual file**, so they go straight into
  `read_lines(path, offset=start_line, limit=end_line - start_line + 1)`.
  Caveat: if the frontmatter changes *line count* (e.g. tags go from unset to set,
  adding a `tags:` line), every chunk's line numbers shift and all of that file's
  chunks get re-embedded. Retagging a file that already has tags changes nothing.
- **Context prefix: yes.** Each chunk's `page_content` is
  `"<title> > <heading>\\n\\n<chunk text>"` (`"<title>\\n\\n<chunk text>"` when there's
  no heading), which helps the embedding place short chunks. The prefix is not part
  of the file and is not counted in `start_line`/`end_line`; `chunk_text(doc)` strips
  it. Consequence: renaming a node re-embeds its chunks.
- **Sizes: 1500 / 200 characters.** ~1500 chars is ~350-400 tokens, comfortably under
  the 2048-token context Ollama gives `nomic-embed-text` by default, yet large enough
  to hold a full paragraph or two (a QASPER section is typically a few thousand
  chars, so it becomes 2-4 chunks). 200 chars of overlap (~1-2 sentences) keeps a
  sentence that straddles a cut retrievable from both sides.
- **Heading:** the markdown (ATX `#`) heading path in effect at the chunk's first line,
  outermost first, joined by `" > "` -- e.g. `"Methods > Data"`. A chunk starting on a
  heading line includes that heading. Headings inside fenced code blocks are ignored.
  `None` when no heading precedes the chunk.

Imports kb.storage.db / kb.okf / kb.storage.models only (kb.service will import this module).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from sqlalchemy import select
from sqlalchemy.orm import undefer

from kb.okf import body_line_offset
from kb.storage.db import SessionLocal
from kb.storage.models import File

DEFAULT_CHUNK_SIZE = 1500
DEFAULT_CHUNK_OVERLAP = 200
HEADING_SEPARATOR = " > "
CONTEXT_SEPARATOR = "\n\n"

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")


class FileNodeLoader(BaseLoader):
    """One `Document` per active (non-deleted) file. `file_ids=None` loads every such
    file; otherwise only those ids (ids that are missing, deleted or folders are
    silently not yielded).

    `page_content` is `node.content`; metadata is `file_id` (str), `title`, and
    `line_offset` (lines before the content in the rendered virtual file, consumed
    and dropped by `split_documents`).
    """

    def __init__(self, file_ids: Iterable[uuid.UUID] | None = None, *, session_factory=SessionLocal):
        self.file_ids = None if file_ids is None else list(dict.fromkeys(file_ids))
        self.session_factory = session_factory

    def lazy_load(self) -> Iterator[Document]:
        stmt = select(File).where(File.deleted_at.is_(None)).options(undefer(File.content))
        if self.file_ids is not None:
            if not self.file_ids:
                return
            stmt = stmt.where(File.id.in_(self.file_ids))
        with self.session_factory() as session:
            for node in session.scalars(stmt.order_by(File.id)):
                yield node_document(node)


def node_document(node: File) -> Document:
    """The loader's `Document` for one file -- also for a file that isn't committed (or
    even flushed) yet, which is how writes stage their chunks before the commit."""
    return Document(
        page_content=node.content,
        metadata={
            "file_id": str(node.id),
            "title": node.title,
            "line_offset": body_line_offset(node),
        },
    )


def heading_paths(text: str) -> list[str | None]:
    """For each line of `text` (split on "\\n"), the heading path in effect there."""
    stack: list[tuple[int, str]] = []
    fence: str | None = None
    out: list[str | None] = []
    for line in text.split("\n"):
        m_fence = _FENCE_RE.match(line)
        if fence is not None:
            if m_fence and m_fence.group(1) == fence:
                fence = None
        elif m_fence:
            fence = m_fence.group(1)
        else:
            m = _HEADING_RE.match(line)
            if m and m.group(2).strip():
                level = len(m.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, m.group(2).strip()))
        out.append(HEADING_SEPARATOR.join(t for _, t in stack) if stack else None)
    return out


def annotate_chunk(chunk: Document, source_text: str, paths: list[str | None] | None = None) -> Document:
    """Converts a splitter chunk's `start_index` into `start_line`/`end_line` (1-based,
    shifted by `line_offset`) and `heading`; drops `start_index`/`line_offset`. Leaves
    `page_content` as the raw chunk text."""
    meta = dict(chunk.metadata)
    start_index = meta.pop("start_index")
    offset = meta.pop("line_offset", 0)
    if start_index < 0:  # splitter couldn't locate the chunk; shouldn't happen
        start_index = max(source_text.find(chunk.page_content), 0)
    first = source_text.count("\n", 0, start_index)  # 0-based line within the body
    last = first + chunk.page_content.count("\n")
    if paths is None:
        paths = heading_paths(source_text)
    meta["heading"] = paths[first]
    meta["start_line"] = offset + first + 1
    meta["end_line"] = offset + last + 1
    return Document(page_content=chunk.page_content, metadata=meta)


def _with_context(chunk: Document) -> Document:
    title = chunk.metadata.get("title")
    heading = chunk.metadata.get("heading")
    label = HEADING_SEPARATOR.join(p for p in (title, heading) if p)
    if not label:
        return chunk
    return Document(page_content=f"{label}{CONTEXT_SEPARATOR}{chunk.page_content}", metadata=chunk.metadata)


def chunk_text(doc: Document) -> str:
    """The original file text of a chunk produced by `split_documents` (context prefix
    removed) -- i.e. exactly lines `start_line..end_line` of the virtual file, modulo
    leading/trailing whitespace trimmed by the splitter."""
    label = HEADING_SEPARATOR.join(
        p for p in (doc.metadata.get("title"), doc.metadata.get("heading")) if p
    )
    prefix = f"{label}{CONTEXT_SEPARATOR}" if label else ""
    if prefix and doc.page_content.startswith(prefix):
        return doc.page_content[len(prefix) :]
    return doc.page_content


def split_documents(
    docs: Iterable[Document],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Document]:
    """Markdown-aware split of loader documents into annotated chunks (see module
    docstring). Whitespace-only documents produce no chunks."""
    splitter = RecursiveCharacterTextSplitter.from_language(
        Language.MARKDOWN,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
    )
    out: list[Document] = []
    for doc in docs:
        if not doc.page_content.strip():
            continue
        paths = heading_paths(doc.page_content)
        for chunk in splitter.split_documents([doc]):
            annotated = annotate_chunk(chunk, doc.page_content, paths)
            out.append(_with_context(annotated))
    return out
