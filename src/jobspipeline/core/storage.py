"""
Persistence layer — turns a transient fetch into durable, queryable data.

Jobs are stored in a local SQLite file (jobs.db) keyed by `dedup_key`, the
fingerprint already on every Job. Because that key is the PRIMARY KEY, storing
is naturally idempotent: re-running never creates duplicates, and we can tell
exactly which postings are NEW since the last run vs already known.

The domain `Job` stays a pure Pydantic object. This module maps it to a thin
`JobRecord` row — a few scalar columns the future API/UI will filter on, plus
a JSON blob holding the rest of the job for fidelity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import JSON, Column, func
from sqlmodel import Field, Session, SQLModel, create_engine, select

from ..schemas import Job

# jobs.db lives at the project root (storage.py is at src/jobspipeline/core/)
DB_PATH = Path(__file__).resolve().parents[3] / "jobs.db"
engine = create_engine(f"sqlite:///{DB_PATH}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRecord(SQLModel, table=True):
    __tablename__ = "jobs"

    # the dedup fingerprint IS the primary key -> idempotent storage
    dedup_key: str = Field(primary_key=True)

    # scalar columns the UI/API will query and filter on
    source: str = Field(index=True)
    company: str = Field(index=True)
    title: str
    location: Optional[str] = Field(default=None)
    seniority: str = Field(default="unknown", index=True)
    posted_at: Optional[datetime] = Field(default=None, index=True)
    apply_url: Optional[str] = Field(default=None)

    # everything else, full fidelity (minus bulky fields we can re-fetch cheaply)
    data: dict = Field(sa_column=Column(JSON))

    # bookkeeping — this is what powers "new since last run"
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)


@dataclass
class StoreResult:
    new: int        # dedup_keys never seen before this run
    seen: int       # already in the DB; we refreshed last_seen
    total: int      # total rows in the DB now


def init_db() -> None:
    """Create the table if it doesn't exist. Safe to call every run."""
    SQLModel.metadata.create_all(engine)


def _to_record(job: Job, now: datetime) -> JobRecord:
    location = job.locations[0].raw if job.locations else None
    return JobRecord(
        dedup_key=job.dedup_key,
        source=job.source.value,
        company=job.company,
        title=job.title,
        location=location,
        seniority=job.seniority.value,
        posted_at=job.posted_at,
        apply_url=job.apply_url,
        # drop `raw` (huge, and boards are free to re-fetch) and the duplicate
        # HTML; keep the plain-text description for the scoring stage later.
        data=job.model_dump(mode="json", exclude={"raw", "description_html"}),
        first_seen=now,
        last_seen=now,
    )


def store_jobs(jobs: list[Job]) -> StoreResult:
    """Upsert jobs by dedup_key. New ones inserted, known ones refreshed."""
    new = seen = 0
    now = _utcnow()
    with Session(engine) as session:
        for job in jobs:
            existing = session.get(JobRecord, job.dedup_key)
            fresh = _to_record(job, now)
            if existing is None:
                session.add(fresh)
                new += 1
            else:
                # keep original first_seen; refresh the rest
                existing.title = fresh.title
                existing.location = fresh.location
                existing.seniority = fresh.seniority
                existing.posted_at = fresh.posted_at
                existing.apply_url = fresh.apply_url
                existing.data = fresh.data
                existing.last_seen = now
                session.add(existing)
                seen += 1
        session.commit()
        total = session.exec(select(func.count(JobRecord.dedup_key))).one()
    return StoreResult(new=new, seen=seen, total=total)