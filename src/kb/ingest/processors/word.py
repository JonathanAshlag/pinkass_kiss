"""
Word processor: `.docx` converted to markdown with python-docx (headings, paragraphs,
list items and tables, in document order). Skipped in favor of Docling when the optional
`docling` extra is installed (see `default_registry`). Converted, so the original is
retained in the blob store when one is configured.
"""

from pathlib import Path

from kb.ingest.processors.base import ProcessedDocument
from kb.ingest.processors.markdown import title_from_markdown
from kb.ingest.processors.spreadsheet import rows_to_markdown


def _paragraph_md(p) -> str:
    text = p.text.strip()
    if not text:
        return ""
    style = (p.style.name or "") if p.style is not None else ""
    if style == "Title":
        return f"# {text}"
    if style.startswith("Heading "):
        suffix = style.removeprefix("Heading ").strip()
        level = int(suffix) if suffix.isdigit() else 1
        return f"{'#' * min(max(level, 1), 6)} {text}"
    if style.startswith("List Number"):
        return f"1. {text}"
    if style.startswith("List"):
        return f"- {text}"
    return text


class WordProcessor:
    name = "word"
    extensions = frozenset({".docx"})
    retain_original = True

    def process(self, path: Path) -> ProcessedDocument:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(str(path))
        blocks = []
        for child in doc.element.body.iterchildren():
            if child.tag.endswith("}p"):
                block = _paragraph_md(Paragraph(child, doc))
            elif child.tag.endswith("}tbl"):
                table = Table(child, doc)
                block = rows_to_markdown([[c.text.strip() for c in row.cells] for row in table.rows])
            else:
                continue
            if block:
                blocks.append(block)
        if not blocks:
            raise ValueError("no text extracted from document")
        content = "\n\n".join(blocks) + "\n"
        return ProcessedDocument(title=title_from_markdown(content, path), content=content)
