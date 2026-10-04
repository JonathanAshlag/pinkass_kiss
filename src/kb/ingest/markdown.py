"""Markdown processor: content stored verbatim (no frontmatter parsing)."""

import re
from pathlib import Path

from kb.ingest.base import ProcessedDocument

_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


class MarkdownProcessor:
    name = "markdown"
    extensions = frozenset({".md", ".markdown"})

    def process(self, path: Path) -> ProcessedDocument:
        content = path.read_text(encoding="utf-8")  # UnicodeDecodeError -> reported as failed
        match = _H1.search(content)
        title = match.group(1) if match else path.stem
        return ProcessedDocument(title=title, content=content)
