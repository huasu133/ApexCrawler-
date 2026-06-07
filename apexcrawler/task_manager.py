"""Task manager with SQLite persistence for crawl task lifecycle.
Supports: create, track, pause, resume, cancel, cleanup.
SQLite with WAL mode for concurrent access safety.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CrawlTask:
    id: str
    url: str
    status: TaskStatus = TaskStatus.PENDING
    engine: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    result: dict | None = None
    error: str = ""
    progress: int = 0  # 0-100


class TaskManager:
    """SQLite-backed async-safe task manager."""

    _instance: TaskManager | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        db_dir = os.path.expanduser("~/.apexcrawler")
        os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(os.path.join(db_dir, "tasks.db"), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                engine TEXT DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                progress INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

    async def create_task(self, url: str, engine: str = "") -> CrawlTask:
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        async with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, url, status, engine, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, url, TaskStatus.PENDING, engine, now, now),
            )
            self._conn.commit()
        return CrawlTask(id=task_id, url=url, status=TaskStatus.PENDING, engine=engine, created_at=now, updated_at=now)

    async def get_task(self, task_id: str) -> CrawlTask | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    async def list_tasks(self, limit: int = 50, status: str | None = None) -> list[CrawlTask]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    async def update_task(self, task_id: str, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k == 'result' and v is not None:
                v = json.dumps(v)
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(task_id)
        async with self._lock:
            self._conn.execute(f"UPDATE tasks SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)
            self._conn.commit()

    async def run_task(self, task_id: str, pipeline_fn, ctx) -> None:
        """Execute a crawl task with pause/cancel support."""
        pause_event = asyncio.Event()
        pause_event.set()  # Not paused initially
        self._pause_events[task_id] = pause_event

        async def _run():
            try:
                # Check pause state before starting
                await pause_event.wait()

                await self.update_task(task_id, status=TaskStatus.RUNNING.value, progress=10)

                # Execute pipeline (checks pause events between stages)
                success, result_ctx = await pipeline_fn(ctx, pause_event)

                if success:
                    await self.update_task(
                        task_id, status=TaskStatus.COMPLETED.value, progress=100,
                        result={"html_length": len(result_ctx.raw_html or ""), "engine": result_ctx.selected_engine},
                    )
                else:
                    await self.update_task(
                        task_id, status=TaskStatus.FAILED.value, progress=0,
                        error=str(result_ctx.fatal_error or result_ctx.stage_errors),
                    )
            except asyncio.CancelledError:
                await self.update_task(task_id, status=TaskStatus.CANCELLED.value)
            except Exception as e:
                await self.update_task(task_id, status=TaskStatus.FAILED.value, error=str(e))

        self._active_tasks[task_id] = asyncio.create_task(_run())

    async def pause_task(self, task_id: str) -> bool:
        if task_id in self._pause_events:
            self._pause_events[task_id].clear()
            await self.update_task(task_id, status=TaskStatus.PAUSED.value)
            return True
        return False

    async def resume_task(self, task_id: str) -> bool:
        if task_id in self._pause_events:
            self._pause_events[task_id].set()
            await self.update_task(task_id, status=TaskStatus.RUNNING.value)
            return True
        return False

    async def cancel_task(self, task_id: str) -> bool:
        if task_id in self._active_tasks:
            self._active_tasks[task_id].cancel()
            if task_id in self._pause_events:
                self._pause_events[task_id].set()
            await self.update_task(task_id, status=TaskStatus.CANCELLED.value)
            return True
        return False

    async def get_metrics(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()
        metrics = {"total": 0}
        for status, count in rows:
            metrics[status] = count
            metrics["total"] += count
        return metrics

    def _row_to_task(self, row) -> CrawlTask:
        return CrawlTask(
            id=row[0], url=row[1], status=TaskStatus(row[2]) if row[2] else TaskStatus.PENDING,
            engine=row[3] or "", created_at=row[4] or 0, updated_at=row[5] or 0,
            result=json.loads(row[6]) if row[6] else None, error=row[7] or "", progress=row[8] or 0,
        )

    def task_paused(self, task_id: str) -> bool:
        event = self._pause_events.get(task_id)
        return event is not None and not event.is_set()
