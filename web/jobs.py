from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

JOB_TTL = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(tz=UTC)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    ERROR = "error"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.QUEUED
    output_dir: Path | None = None
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    error: str | None = None

    def elapsed_s(self) -> float | None:
        if self.started_at is None:
            return None
        return (_now() - self.started_at).total_seconds()

    def as_response(self) -> dict:
        return {
            "status": self.status.value,
            "elapsed_s": self.elapsed_s(),
            "error": self.error,
        }


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def create(self, job_id: str) -> Job:
        job = Job(id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **kwargs) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for k, v in kwargs.items():
                    setattr(job, k, v)

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job and job.output_dir and job.output_dir.exists():
            shutil.rmtree(job.output_dir, ignore_errors=True)

    def cleanup_expired(self) -> None:
        now = _now()
        with self._lock:
            expired = [
                jid for jid, job in self._jobs.items() if now - job.created_at > JOB_TTL
            ]
        for jid in expired:
            self.delete(jid)


store = JobStore()
