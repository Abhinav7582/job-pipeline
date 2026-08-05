"""
Persistence layer — durable, queryable storage for jobs and scored targets.

Two tables:
  - jobs    : one row per posting, keyed by dedup_key (what the world posted)
  - targets : one row per scored job, keyed by dedup_key (what WE decided)

The domain objects (`Job`, `Target`) stay pure Pydantic; this module maps them
to rows and rebuilds them on read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import JSON, Column, func
from sqlmodel import Field, Session, SQLModel, create_engine, select

from ..schemas import Job
from ..targets import Target

DB_PATH = Path(__file__).resolve().parents[3] / "jobs.db"
engine = create_engine(f"sqlite:///{DB_PATH}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRecord(SQLModel, table=True):
    __tablename__ = "jobs"

    dedup_key: str = Field(primary_key=True)
    source: str = Field(index=True)
    company: str = Field(index=True)
    title: str
    location: Optional[str] = Field(default=None)
    seniority: str = Field(default="unknown", index=True)
    posted_at: Optional[datetime] = Field(default=None, index=True)
    apply_url: Optional[str] = Field(default=None)
    data: dict = Field(sa_column=Column(JSON))
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)


class TargetRecord(SQLModel, table=True):
    __tablename__ = "targets"

    dedup_key: str = Field(primary_key=True)
    status: str = Field(index=True)
    score: Optional[int] = Field(default=None, index=True)
    score_reasons: Optional[str] = Field(default=None)
    channel: str = Field(default="application")
    title: str
    company: str = Field(index=True)
    location: Optional[str] = Field(default=None)
    apply_url: Optional[str] = Field(default=None)
    source: str = Field(default="")
    scored_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


@dataclass
class StoreResult:
    new: int
    seen: int
    total: int


def init_db() -> None:
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
        data=job.model_dump(mode="json", exclude={"raw", "description_html"}),
        first_seen=now,
        last_seen=now,
    )


def store_jobs(jobs: list[Job]) -> StoreResult:
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


def load_jobs() -> list[Job]:
    with Session(engine) as session:
        records = session.exec(select(JobRecord)).all()
    return [Job(**rec.data) for rec in records]


def scored_keys() -> set[str]:
    """dedup_keys that already have a score — so we never pay to score twice."""
    with Session(engine) as session:
        rows = session.exec(
            select(TargetRecord.dedup_key).where(TargetRecord.status == "scored")
        ).all()
    return set(rows)


def store_targets(targets: list[Target]) -> None:
    now = _utcnow()
    with Session(engine) as session:
        for t in targets:
            job = t.job
            rec = session.get(TargetRecord, t.dedup_key)
            if rec is None:
                rec = TargetRecord(dedup_key=t.dedup_key, title=job.title,
                                   company=job.company, created_at=now)
            rec.status = t.status.value
            rec.score = t.score
            rec.score_reasons = t.score_reasons
            rec.channel = t.channel.value
            rec.title = job.title
            rec.company = job.company
            rec.location = job.locations[0].raw if job.locations else None
            rec.apply_url = job.apply_url
            rec.source = job.source.value
            rec.scored_at = t.scored_at
            rec.updated_at = now
            session.add(rec)
        session.commit()


def top_targets(limit: int = 20) -> list[TargetRecord]:
    with Session(engine) as session:
        stmt = (
            select(TargetRecord)
            .where(TargetRecord.status == "scored")
            .order_by(TargetRecord.score.desc())
            .limit(limit)
        )
        return list(session.exec(stmt).all())