"""
The ingestion extension point: a `Processor` turns one file on disk into a
`ProcessedDocument` (title + markdown content + optional extra `files` columns), and a
`ProcessorRegistry` maps file extensions to processors.

Adding a format (PDF, docx, ...) means writing one class that satisfies `Processor` and
registering it in `default_registry()` -- the folder walker (`kb.ingest.folder`) never
needs to change. Formats a LangChain loader already handles need no class at all: wrap
the loader in `kb.ingest.processors.converters.LoaderProcessor`.

A processor may also set `retain_original = True` (optional, default False) when its
`content` is a conversion rather than the file itself; the walker then keeps the raw
bytes in the blob store (kb.storage.blobs) and records them in the node's `blob_*` columns.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ProcessedDocument:
    title: str
    content: str
    # Extra `files` columns the processor wants set (aliases, description, ...).
    extra: dict[str, Any] = field(default_factory=dict)


class Processor(Protocol):
    name: str
    extensions: frozenset[str]  # lowercase, with the dot: {".md", ".markdown"}

    def process(self, path: Path) -> ProcessedDocument:
        """Read `path` into a document. Raises on unreadable input; the walker catches
        it and reports the file as failed."""
        ...


class ProcessorRegistry:
    def __init__(self) -> None:
        self._by_extension: dict[str, Processor] = {}

    def register(self, processor: Processor) -> None:
        for ext in processor.extensions:
            ext = ext.lower()
            if ext in self._by_extension:
                raise ValueError(
                    f"extension {ext} already handled by {self._by_extension[ext].name}"
                )
        for ext in processor.extensions:
            self._by_extension[ext.lower()] = processor

    def for_path(self, path: Path) -> Processor | None:
        return self._by_extension.get(path.suffix.lower())

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset(self._by_extension)


def default_registry() -> ProcessorRegistry:
    """A fresh registry with every built-in processor."""
    from kb.ingest.processors.converters import builtin_loader_processors
    from kb.ingest.processors.markdown import MarkdownProcessor

    from kb.ingest.processors.spreadsheet import SpreadsheetProcessor
    from kb.ingest.processors.text import CodeProcessor, PlainTextProcessor
    from kb.ingest.processors.word import WordProcessor

    registry = ProcessorRegistry()
    registry.register(MarkdownProcessor())
    for processor in builtin_loader_processors():
        registry.register(processor)
    registry.register(PlainTextProcessor())
    registry.register(CodeProcessor())
    registry.register(SpreadsheetProcessor())
    # Docling (if installed) already takes .docx; the python-docx one is the fallback.
    if ".docx" not in registry.supported_extensions:
        registry.register(WordProcessor())
    return registry
