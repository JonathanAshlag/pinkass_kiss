"""Markdown processor: content stored verbatim (no frontmatter parsing), title = file stem."""

from pathlib import Path

from kb.ingest.processors.base import ProcessedDocument


class MarkdownProcessor:
    name = "markdown"
    extensions = frozenset({".md", ".markdown"})

    def process(self, path: Path) -> ProcessedDocument:
        content = path.read_text(encoding="utf-8")  # UnicodeDecodeError -> reported as failed
        return ProcessedDocument(title=path.stem, content=content)
