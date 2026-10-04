"""Folder ingestion with pluggable per-format processors. See kb.ingest.base."""

from kb.ingest.base import ProcessedDocument, Processor, ProcessorRegistry, default_registry
from kb.ingest.folder import IngestReport, ingest_folder
from kb.ingest.upload import UploadError, ingest_upload

__all__ = [
    "IngestReport",
    "UploadError",
    "ingest_upload",
    "ProcessedDocument",
    "Processor",
    "ProcessorRegistry",
    "default_registry",
    "ingest_folder",
]
