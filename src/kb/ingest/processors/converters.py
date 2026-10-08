"""
Processors backed by LangChain document loaders: binary formats (PDF, DOCX, PPTX, HTML)
converted to markdown. Unlike markdown, the stored content isn't the original, so these
set `retain_original = True` and the folder walker keeps the raw bytes in the blob store
(see kb.storage.blobs) when one is configured.

Built-ins: PyMuPDF4LLM for `.pdf`, Docling for `.docx .pptx .xlsx .csv .html .htm`. No
fallbacks: both libraries are core dependencies, and images aren't ingested.
"""

from collections.abc import Callable
from pathlib import Path

from langchain_core.document_loaders import BaseLoader

from kb.ingest.processors.base import ProcessedDocument

LoaderFactory = Callable[[Path], BaseLoader]

DOCLING_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".csv", ".html", ".htm"}


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
        return ProcessedDocument(title=path.stem, content=content + "\n")


def pymupdf4llm_processor() -> LoaderProcessor:
    from langchain_pymupdf4llm import PyMuPDF4LLMLoader

    # mode="single": one document for the whole PDF (pages joined by a `-----` rule).
    return LoaderProcessor(
        "pymupdf4llm", {".pdf"}, lambda path: PyMuPDF4LLMLoader(str(path), mode="single")
    )


def docling_processor() -> LoaderProcessor:
    from langchain_docling import DoclingLoader
    from langchain_docling.loader import ExportType

    return LoaderProcessor(
        "docling",
        DOCLING_EXTENSIONS,
        lambda path: DoclingLoader(file_path=str(path), export_type=ExportType.MARKDOWN),
    )


def builtin_loader_processors() -> list[LoaderProcessor]:
    return [pymupdf4llm_processor(), docling_processor()]
