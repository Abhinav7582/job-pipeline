"""
Core data schemas for the job pipeline.

`Job` is the normalized shape that EVERY source adapter must produce.
It represents a job posting exactly as the outside world published it —
it is the source-of-record, treated as immutable once created.

Our own pipeline state (fit score, status, which channel, generated drafts)
deliberately does NOT live here. It belongs on a separate `Target` record
that *wraps* a Job, so we never mix "what the world posted" with "what we
decided about it". We build `Target` later, alongside the review queue.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Controlled vocabularies                                                       #
# Enums keep every adapter speaking the same language. A Greenhouse "Full-time"
# and a Lever "fulltime" both normalize to EmploymentType.full_time.            #
# --------------------------------------------------------------------------- #

class SourceType(str, Enum):
    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    workable = "workable"
    telegram = "telegram"
    linkedin_email = "linkedin_email"
    manual = "manual"          # pasted by hand
    other = "other"


class EmploymentType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    internship = "internship"
    temporary = "temporary"
    other = "other"


class RemoteType(str, Enum):
    on_site = "on_site"
    hybrid = "hybrid"
    remote = "remote"
    unknown = "unknown"


class Seniority(str, Enum):
    intern = "intern"
    entry = "entry"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    principal = "principal"
    director = "director"
    executive = "executive"
    unknown = "unknown"        # default — let the scoring stage infer later


# --------------------------------------------------------------------------- #
# Sub-objects                                                                   #
# --------------------------------------------------------------------------- #

class Location(BaseModel):
    raw: Optional[str] = None          # exactly as the source gave it
    city: Optional[str] = None
    region: Optional[str] = None       # state / province
    country: Optional[str] = None      # ISO 3166 alpha-2 preferred, e.g. "IN"
    remote: RemoteType = RemoteType.unknown


class Compensation(BaseModel):
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    currency: Optional[str] = None     # ISO 4217, e.g. "USD", "INR"
    period: Optional[str] = None       # "year" | "month" | "hour"
    raw: Optional[str] = None          # keep the original string if unparsed


# --------------------------------------------------------------------------- #
# The Job — the one shape the whole system depends on                           #
# --------------------------------------------------------------------------- #

class Job(BaseModel):
    """A normalized job posting. Every adapter outputs exactly this."""

    # --- provenance: where this came from -------------------------------- #
    source: SourceType
    source_job_id: str                 # the posting's id within that source
    source_url: Optional[str] = None
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # --- core ------------------------------------------------------------ #
    title: str
    company: str
    description: Optional[str] = None        # plain text (HTML stripped)
    description_html: Optional[str] = None   # original markup, if worth keeping

    # --- classification -------------------------------------------------- #
    department: Optional[str] = None
    employment_type: EmploymentType = EmploymentType.other
    seniority: Seniority = Seniority.unknown
    locations: list[Location] = Field(default_factory=list)

    # --- comp & dates ---------------------------------------------------- #
    compensation: Optional[Compensation] = None
    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # --- apply ----------------------------------------------------------- #
    apply_url: Optional[str] = None

    # --- escape hatch ---------------------------------------------------- #
    # Always stash the source's original payload. When a parse is wrong or a
    # field is missing, you re-derive from here instead of re-fetching.
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("title", "company")
    @classmethod
    def _strip_required_text(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("title and company cannot be blank")
        return cleaned

    @property
    def dedup_key(self) -> str:
        """
        Stable fingerprint so the same role seen from two sources
        (e.g. a company's Greenhouse board AND a LinkedIn alert email)
        collapses into one target. Based on company + title + first location.
        """
        first_loc = ""
        if self.locations:
            loc = self.locations[0]
            first_loc = loc.city or loc.raw or ""
        basis = "|".join(_normalize(p) for p in (self.company, self.title, first_loc))
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for dedup only."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


# --------------------------------------------------------------------------- #
# Quick smoke test:  python -m jobpipeline.schemas                             #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    job = Job(
        source=SourceType.greenhouse,
        source_job_id="123456",
        source_url="https://boards.greenhouse.io/example/jobs/123456",
        title="  Senior Data Analyst  ",
        company="Example Inc",
        department="Analytics",
        employment_type=EmploymentType.full_time,
        seniority=Seniority.senior,
        locations=[Location(raw="Bengaluru, India", city="Bengaluru",
                            country="IN", remote=RemoteType.hybrid)],
        raw={"id": 123456, "title": "Senior Data Analyst"},
    )
    print(job.model_dump_json(indent=2))
    print("dedup_key:", job.dedup_key)