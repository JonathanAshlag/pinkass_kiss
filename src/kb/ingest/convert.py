"""
Runs processors over many files, the CPU-heavy ones in parallel across processes.

Converting a PDF/DOCX is pure-Python, CPU-bound work (PyMuPDF4LLM ~3.5 pages/s on one
core), so threads don't help; a process pool does. Only processors that set
`cpu_bound = True` (the LangChain loader ones) go to the pool, even a single file: a
worker can be killed when its conversion hangs (`KB_CONVERT_TIMEOUT`), and its crash
(MuPDF segfault on a malformed PDF) doesn't take the API down. Markdown/text reads are
cheaper than shipping them to another process. A processor that can't be pickled (e.g.
a test's local class, a lambda loader factory) runs in-process instead, so any
`Processor` still works, just not in parallel.

The pool is created on first use and kept for the life of the process (workers pay the
import / model-loading cost once, not per upload: under a second, PyMuPDF4LLM's ~15 MB of
ONNX layout models; Docling's office/HTML pipelines load none) and shared by concurrent
requests. `KB_INGEST_WORKERS` sets its size: default min(4, CPUs available to this
process), `1` = no pool. Note that in a container `os.cpu_count()` is the *node's* CPU
count, not the pod's limit, so set it to match the pod's CPU limit.
"""

import logging
import multiprocessing
import pickle
import threading
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from kb.ingest.processors.base import ProcessedDocument, Processor
from kb.settings import get_settings

log = logging.getLogger(__name__)

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def ingest_worker_count() -> int:
    return get_settings().ingest_workers


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            # spawn, not fork: the parent has threads (uvicorn, boto3), and forking
            # a threaded process can deadlock the child.
            _pool = ProcessPoolExecutor(ingest_worker_count(), mp_context=multiprocessing.get_context("spawn"))
        return _pool


def _discard_pool(broken: ProcessPoolExecutor, *, kill: bool = False) -> None:
    """Drop a pool whose worker died (segfault, OOM kill) or hung, so the next call starts
    a new one. `kill`: stop its workers first, since shutdown alone never stops a hung one."""
    global _pool
    with _pool_lock:
        if _pool is broken:
            _pool = None
    if kill:
        # ponytail: private attribute; use ProcessPoolExecutor.kill_workers() once we require 3.14.
        for process in list((broken._processes or {}).values()):
            process.kill()
    broken.shutdown(wait=False, cancel_futures=True)


def _picklable(processor: Processor) -> bool:
    try:
        pickle.dumps(processor)
    except Exception:  # noqa: BLE001 -- PicklingError, AttributeError, TypeError, ...
        return False
    return True


def _run(processor: Processor, path: Path) -> ProcessedDocument:
    return processor.process(path)


def _describe_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def convert_documents(items: list[tuple[Path, Processor]]) -> list[ProcessedDocument | str]:
    """`processor.process(path)` for each item, in order: the document, or the error
    message if it raised (never raises itself). CPU-bound, picklable processors run in
    the shared process pool when there is more than one worker, each limited to
    `KB_CONVERT_TIMEOUT`; everything else runs here, while the pool works."""
    results: list[ProcessedDocument | str | None] = [None] * len(items)
    offload = set()
    if ingest_worker_count() >= 2:
        offload = {i for i, (_, proc) in enumerate(items) if getattr(proc, "cpu_bound", False) and _picklable(proc)}

    futures: dict[int, Future] = {}
    if offload:
        pool = _get_pool()
        try:
            for i in sorted(offload):
                path, proc = items[i]
                futures[i] = pool.submit(_run, proc, path)
        except BrokenProcessPool:  # broke since the last call: start over, once
            for f in futures.values():
                f.cancel()
            _discard_pool(pool)
            pool, futures = _get_pool(), {}
            for i in sorted(offload):
                path, proc = items[i]
                futures[i] = pool.submit(_run, proc, path)

    for i, (path, proc) in enumerate(items):
        if i not in futures:
            try:
                results[i] = proc.process(path)
            except Exception as exc:  # noqa: BLE001 -- any processor error is per-file
                results[i] = _describe_exception(exc)

    timeout = get_settings().convert_timeout
    for i, future in futures.items():
        try:
            # Counted from when we start waiting, so queueing behind other files eats into it.
            results[i] = future.result(timeout=timeout)
        except TimeoutError:
            # Kills the pool's other conversions too (this upload's and concurrent ones'),
            # which then fail like a crash; the ingest fails as a whole anyway.
            _discard_pool(pool, kill=True)
            results[i] = f"conversion timed out after {timeout:g}s"
        except CancelledError:
            results[i] = "converter pool was reset (another conversion timed out or crashed)"
        except BrokenProcessPool as exc:
            # A worker died mid-conversion; we can't tell which file did it, so every file
            # still pending on the pool is reported (the ingest fails as a whole anyway).
            _discard_pool(pool)
            results[i] = f"converter process crashed: {_describe_exception(exc)}"
        except Exception as exc:  # noqa: BLE001
            results[i] = _describe_exception(exc)
    return results  # type: ignore[return-value]
