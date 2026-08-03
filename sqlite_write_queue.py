"""Process-wide single-writer queue for SQLite hot paths.

Reads continue to use short connections via database.get_conn(). Writers that
need to avoid lock storms (hotspot ingest progress, video progress, audit logs)
should submit callables here.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class WriteQueueStats:
    queued: int = 0
    completed: int = 0
    failed: int = 0
    max_wait_ms: float = 0.0
    last_wait_ms: float = 0.0
    lock_retries: int = 0
    queue_depth_peak: int = 0


_stats = WriteQueueStats()
_stats_lock = threading.Lock()
_queue: deque[tuple[Callable[[], Any], threading.Event, list]] = deque()
_queue_condition = threading.Condition()
_worker_started = False
_worker_lock = threading.Lock()


def _record_wait(wait_ms: float) -> None:
    with _stats_lock:
        _stats.last_wait_ms = wait_ms
        _stats.max_wait_ms = max(_stats.max_wait_ms, wait_ms)


def _worker_loop() -> None:
    while True:
        with _queue_condition:
            while not _queue:
                _queue_condition.wait()
            fn, done, box = _queue.popleft()
        started = time.perf_counter()
        try:
            box.append(("ok", fn()))
            with _stats_lock:
                _stats.completed += 1
        except Exception as exc:  # noqa: BLE001 — propagate to caller via box
            box.append(("err", exc))
            with _stats_lock:
                _stats.failed += 1
            logger.exception("SQLite write queue task failed")
        finally:
            _record_wait((time.perf_counter() - started) * 1000)
            done.set()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="sqlite-write-queue", daemon=True)
        thread.start()
        _worker_started = True


def submit_write(fn: Callable[[], Any], *, timeout: float | None = 60.0) -> Any:
    """Run ``fn`` exclusively on the write worker and return its result."""
    _ensure_worker()
    done = threading.Event()
    box: list = []
    with _queue_condition:
        _queue.append((fn, done, box))
        with _stats_lock:
            _stats.queued += 1
            _stats.queue_depth_peak = max(_stats.queue_depth_peak, len(_queue))
        _queue_condition.notify()
    if not done.wait(timeout=timeout):
        raise TimeoutError("SQLite write queue timed out")
    status, payload = box[0]
    if status == "err":
        raise payload
    return payload


def get_write_queue_stats() -> dict:
    with _stats_lock:
        depth = len(_queue)
        return {
            "queued": _stats.queued,
            "completed": _stats.completed,
            "failed": _stats.failed,
            "queue_depth": depth,
            "queue_depth_peak": _stats.queue_depth_peak,
            "last_wait_ms": round(_stats.last_wait_ms, 2),
            "max_wait_ms": round(_stats.max_wait_ms, 2),
            "lock_retries": _stats.lock_retries,
        }


def wal_size_bytes(db_path: Path | None = None) -> int:
    import database as db

    path = Path(db_path or db.DB_PATH)
    wal = path.with_suffix(path.suffix + "-wal")
    try:
        return wal.stat().st_size if wal.exists() else 0
    except OSError:
        return 0


def get_sqlite_health() -> dict:
    stats = get_write_queue_stats()
    stats["wal_size_bytes"] = wal_size_bytes()
    return stats
