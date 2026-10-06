"""Markdown processor: content stored verbatim (no frontmatter parsing)."""

import re
from pathlib import Path

from kb.ingest.processors.base import ProcessedDocument

_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)


def title_from_markdown(content: str, path: Path) -> str:
    """The first `# ` H1 in `content`, else the file name's stem."""
    match = _H1.search(content)
    return match.group(1) if match else path.stem


class MarkdownProcessor:
    name = "markdown"
    extensions = frozenset({".md", ".markdown"})

    def process(self, path: Path) -> ProcessedDocument:
        content = path.read_text(encoding="utf-8")  # UnicodeDecodeError -> reported as failed
        return ProcessedDocument(title=title_from_markdown(content, path), content=content)
