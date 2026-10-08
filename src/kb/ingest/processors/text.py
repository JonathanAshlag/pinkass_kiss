"""
Plain-text processor: `.txt`-like prose stored verbatim. The content is the whole
original, so nothing is retained in the blob store.

Code and config files (`.py`, `.json`, ...) are deliberately not ingested: out of the
product's scope, and they invite using the KB as a code store.
"""

from pathlib import Path

from kb.ingest.processors.base import ProcessedDocument

PROSE_EXTENSIONS = frozenset({".txt", ".text", ".rst", ".log"})


class PlainTextProcessor:
    name = "text"
    extensions = PROSE_EXTENSIONS

    def process(self, path: Path) -> ProcessedDocument:
        content = path.read_text(encoding="utf-8")  # UnicodeDecodeError -> reported as failed
        if not content.strip():
            raise ValueError("file is empty")
        return ProcessedDocument(title=path.stem, content=content)

