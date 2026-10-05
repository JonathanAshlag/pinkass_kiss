"""
Processors backed by LangChain document loaders: binary formats (PDF, DOCX, PPTX, HTML)
converted to markdown. Unlike markdown, the stored content isn't the original, so these
set `retain_original = True` and the folder walker keeps the raw bytes in the blob store
(see kb.blobs) when one is configured.

Built-ins: PyMuPDF4LLM for `.pdf` (always installed), and Docling for
`.pdf .docx .pptx .html .htm` when the optional `langchain-docling` extra is installed
(it then takes over `.pdf`).
"""

from collections.abc import Callable
from pathlib import Path

from langchain_core.document_loaders import BaseLoader

from kb.ingest.base import ProcessedDocument
from kb.ingest.markdown import title_from_markdown

LoaderFactory = Callable[[Path], BaseLoader]


class LoaderProcessor:
    """Adapts any LangChain loader (built per file by `loader_factory(path)`) to the
    `Processor` protocol: the loader's documents are joined into one markdown string.
    Loader metadata is dropped -- none of it (page count, producer, ...) maps to a
    `files` column."""

    retain_original = True

    def __init__(self, name: str, extensions: frozenset[str] | set[str], loader_factory: LoaderFactory) -> None:
        self.name = name
        self.extensions = frozenset(ext.lower() for ext in extensions)
        self.loader_factory = loader_factory

    def process(self, path: Path) -> ProcessedDocument:
        docs = self.loader_factory(path).load()
        content = "\n\n".join(d.page_content.strip() for d in docs if d.page_content.strip())
        if not content:
            raise ValueError("no text extracted (scanned or image-only document?)")
        return ProcessedDocument(title=title_from_markdown(content, path), content=content + "\n")


def pymupdf4llm_processor() -> LoaderProcessor:
    from langchain_pymupdf4llm import PyMuPDF4LLMLoader

    # mode="single": one document for the whole PDF (pages joined by a `-----` rule).
    return LoaderProcessor(
        "pymupdf4llm", {".pdf"}, lambda path: PyMuPDF4LLMLoader(str(path), mode="single")
    )


def docling_available() -> bool:
    try:
        import langchain_docling  # noqa: F401
    except ImportError:
        return False
    return True


def docling_processor() -> LoaderProcessor:
    """Requires the `docling` extra; raises ImportError otherwise."""
    from langchain_docling import DoclingLoader
    from langchain_docling.loader import ExportType

    return LoaderProcessor(
        "docling",
        {".pdf", ".docx", ".pptx", ".html", ".htm"},
        lambda path: DoclingLoader(file_path=str(path), export_type=ExportType.MARKDOWN),
    )


def builtin_loader_processors() -> list[LoaderProcessor]:
    """Docling if installed (it covers .pdf too), else PyMuPDF4LLM for .pdf."""
    if docling_available():
        return [docling_processor()]
    return [pymupdf4llm_processor()]
