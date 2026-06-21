"""
Lever adapter — second ATS, identical contract.

Lever's postings API is public and unauthenticated, returning a company's
whole board as a top-level JSON array:
    GET https://api.lever.co/v0/postings/{token}?mode=json

This file is deliberately shaped exactly like greenhouse.py: fetch, then map
each raw posting into the same `Job`. That symmetry IS the payoff of the
adapter pattern — a whole new source, and nothing else in the system changes.

(The _guess_* helpers are duplicated from greenhouse.py for now; once a third
adapter lands they're worth lifting into a shared sources/_text.py.)
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
    Seniority,
    SourceType,
)
from .base import SourceAdapter

POSTINGS_API = "https://api.lever.co/v0/postings/{token}"

# Lever gives a real commitment string, so we map instead of guessing.
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

# Lever also gives an explicit workplace type — better than inferring from text.
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
        remote = _WORKPLACE_MAP.get(workplace) or _guess_remote(loc_name)

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
            seniority=_guess_seniority(raw["text"]),
            locations=[Location(raw=loc_name, remote=remote)] if loc_name else [],
            compensation=_map_salary(raw.get("salaryRange")),
            posted_at=_from_epoch_ms(raw.get("createdAt")),
            apply_url=raw.get("applyUrl") or raw.get("hostedUrl"),
            raw=raw,
        )


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

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


def _guess_seniority(title: str) -> Seniority:
    t = title.lower()
    if "intern" in t:
        return Seniority.intern
    if "chief" in t or t.startswith("ceo") or t.startswith("cto"):
        return Seniority.executive
    if "director" in t or "vp" in t or "vice president" in t or "head of" in t:
        return Seniority.director
    if "principal" in t:
        return Seniority.principal
    if "staff" in t or "lead" in t:
        return Seniority.lead
    if "senior" in t or "sr." in t or "sr " in t:
        return Seniority.senior
    if "junior" in t or "associate" in t or "entry" in t or "graduate" in t:
        return Seniority.entry
    return Seniority.unknown


def _guess_remote(loc: Optional[str]) -> RemoteType:
    if not loc:
        return RemoteType.unknown
    l = loc.lower()
    if "remote" in l:
        return RemoteType.remote
    if "hybrid" in l:
        return RemoteType.hybrid
    return RemoteType.on_site