"""Folder ingestion with pluggable per-format processors. See kb.ingest.processors.base."""

from kb.ingest.processors.base import ProcessedDocument, Processor, ProcessorRegistry, default_registry
from kb.ingest.folder import FolderPlan, IngestReport, ingest_folder, plan_folder
from kb.ingest.processors.converters import LoaderProcessor
from kb.ingest.plan import PlannedNode, materialize
from kb.ingest.upload import UploadError, ingest_upload

__all__ = [
    "FolderPlan",
    "IngestReport",
    "LoaderProcessor",
    "PlannedNode",
    "materialize",
    "plan_folder",
    "UploadError",
    "ingest_upload",
    "ProcessedDocument",
    "Processor",
    "ProcessorRegistry",
    "default_registry",
    "ingest_folder",
]
