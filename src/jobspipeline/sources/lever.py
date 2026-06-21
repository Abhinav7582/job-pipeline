"""
Lever adapter — second ATS, identical contract.

Lever's postings API is public and unauthenticated, returning a company's
whole board as a top-level JSON array:
    GET https://api.lever.co/v0/postings/{token}?mode=json

Lever gives real commitment / workplace / salary fields, so those map directly
rather than being guessed. The generic title/date helpers come from _text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

import httpx

from ..schemas import (
    Compensation,
    EmploymentType,
    Job,
    Location,
    RemoteType,
    SourceType,
)
from ._text import guess_remote, guess_seniority
from .base import SourceAdapter

POSTINGS_API = "https://api.lever.co/v0/postings/{token}"

_COMMITMENT_MAP = {
    "full-time": EmploymentType.full_time,
    "full time": EmploymentType.full_time,
    "part-time": EmploymentType.part_time,
    "part time": EmploymentType.part_time,
    "contract": EmploymentType.contract,
    "contractor": EmploymentType.contract,
    "intern": EmploymentType.internship,
    "internship": EmploymentType.internship,
    "temporary": EmploymentType.temporary,
}

_WORKPLACE_MAP = {
    "on-site": RemoteType.on_site,
    "onsite": RemoteType.on_site,
    "remote": RemoteType.remote,
    "hybrid": RemoteType.hybrid,
}


class LeverAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.lever

    def fetch(self) -> list[Job]:
        url = POSTINGS_API.format(token=self.company.token)
        resp = httpx.get(url, params={"mode": "json"}, timeout=30.0)
        resp.raise_for_status()          # bad token -> 404 -> caught by the runner
        postings = resp.json()           # Lever returns a top-level JSON array
        return [self._to_job(raw) for raw in postings]

    def _to_job(self, raw: dict[str, Any]) -> Job:
        categories = raw.get("categories") or {}
        loc_name = categories.get("location")
        workplace = (raw.get("workplaceType") or "").lower()
        remote = _WORKPLACE_MAP.get(workplace) or guess_remote(loc_name)

        return Job(
            source=self.source_type,
            source_job_id=str(raw["id"]),
            source_url=raw.get("hostedUrl"),
            title=raw["text"],
            company=self.company.name,
            description=raw.get("descriptionPlain") or None,
            description_html=raw.get("description") or None,
            department=categories.get("department") or categories.get("team"),
            employment_type=_map_commitment(categories.get("commitment")),
            seniority=guess_seniority(raw["text"]),
            locations=[Location(raw=loc_name, remote=remote)] if loc_name else [],
            compensation=_map_salary(raw.get("salaryRange")),
            posted_at=_from_epoch_ms(raw.get("createdAt")),
            apply_url=raw.get("applyUrl") or raw.get("hostedUrl"),
            raw=raw,
        )


def _map_commitment(commitment: Optional[str]) -> EmploymentType:
    if not commitment:
        return EmploymentType.other
    return _COMMITMENT_MAP.get(commitment.strip().lower(), EmploymentType.other)


def _map_salary(salary: Optional[dict]) -> Optional[Compensation]:
    if not salary:
        return None
    return Compensation(
        min_amount=salary.get("min"),
        max_amount=salary.get("max"),
        currency=salary.get("currency"),
        period=salary.get("interval"),
    )


def _from_epoch_ms(ms: Optional[int]) -> Optional[datetime]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, OSError, TypeError):
        return None