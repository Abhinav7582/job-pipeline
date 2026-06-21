"""
The Target — a job plus our decision state.

`Job` (schemas.py) is what the world posted: immutable, source of record.
A `Target` wraps exactly one Job and adds everything WE track about it — a fit
score and the reasoning behind it, where it sits in the pipeline (status), and
which channel we'd use to reach out. This is the record the review queue and UI
will show, and it's the whole reason scoring never mutates the underlying Job.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .schemas import Job


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TargetStatus(str, Enum):
    # --- Phase 2 (scoring) uses these ---
    new = "new"                    # created from a job, not yet scored
    filtered_out = "filtered_out"  # failed the profile's hard filters
    scored = "scored"              # has a fit score
    shortlisted = "shortlisted"    # you've flagged it to act on
    # --- later phases (outreach) will use these ---
    drafted = "drafted"            # outreach drafted, awaiting your review
    approved = "approved"          # you approved the draft
    sent = "sent"                  # application submitted / email sent
    replied = "replied"            # got a response
    rejected = "rejected"          # passed on, by you or them
    archived = "archived"


class Channel(str, Enum):
    application = "application"     # apply through the posting
    cold_email = "cold_email"      # reach out to a person directly


class Target(BaseModel):
    """One job, plus our state about it."""

    job: Job

    status: TargetStatus = TargetStatus.new
    score: Optional[int] = None            # 0-100 fit score from the scorer
    score_reasons: Optional[str] = None    # short rationale, for you to read
    channel: Channel = Channel.application  # set properly at the outreach phase

    scored_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def dedup_key(self) -> str:
        """A Target is identified by the job it wraps."""
        return self.job.dedup_key


if __name__ == "__main__":
    from .schemas import Location, RemoteType, Seniority, SourceType

    job = Job(
        source=SourceType.greenhouse,
        source_job_id="123456",
        title="Senior Data Analyst",
        company="Example Inc",
        seniority=Seniority.senior,
        locations=[Location(raw="Bengaluru, India", city="Bengaluru",
                            country="IN", remote=RemoteType.hybrid)],
    )
    target = Target(
        job=job,
        status=TargetStatus.scored,
        score=87,
        score_reasons="Senior level, AdTech-adjacent, matches PySpark/Databricks stack.",
        scored_at=_utcnow(),
    )
    print(f"{target.score}  {target.job.title} @ {target.job.company}  [{target.status.value}]")
    print(f"  reasons: {target.score_reasons}")
    print(f"  dedup_key: {target.dedup_key}")