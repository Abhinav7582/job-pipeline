"""
Ashby adapter — third ATS, identical contract.

Ashby's public posting API returns a company's whole board in one call:
    GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true

Ashby has the cleanest compensation data of the public ATS feeds, so we request
it and capture the salary summary when present. Two quirks handled below:
  - an invalid/empty board returns {"jobs": []}, not a 404
  - the feed includes unlisted/draft roles, so we keep only isListed=true

(Like lever.py, the _guess_* / _parse_dt helpers are duplicated for now — with a
third adapter they're finally worth lifting into a shared sources/_text.py.)
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

JOB_BOARD_API = "https://api.ashbyhq.com/posting-api/job-board/{token}"

_EMPLOYMENT_MAP = {
    "fulltime": EmploymentType.full_time,
    "parttime": EmploymentType.part_time,
    "intern": EmploymentType.internship,
    "contract": EmploymentType.contract,
    "temporary": EmploymentType.temporary,
}

_WORKPLACE_MAP = {
    "remote": RemoteType.remote,
    "on site": RemoteType.on_site,
    "onsite": RemoteType.on_site,
    "hybrid": RemoteType.hybrid,
}


class AshbyAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.ashby

    def fetch(self) -> list[Job]:
        url = JOB_BOARD_API.format(token=self.company.token)
        resp = httpx.get(url, params={"includeCompensation": "true"}, timeout=30.0)
        resp.raise_for_status()
        payload = resp.json()
        return [
            self._to_job(raw)
            for raw in payload.get("jobs", [])
            if raw.get("isListed", True)          # drop unlisted / draft roles
        ]

    def _to_job(self, raw: dict[str, Any]) -> Job:
        title = raw["title"]
        loc_name = raw.get("location")
        workplace = (raw.get("workplaceType") or "").lower()
        remote = _WORKPLACE_MAP.get(workplace)
        if remote is None:
            remote = RemoteType.remote if raw.get("isRemote") else _guess_remote(loc_name)

        return Job(
            source=self.source_type,
            source_job_id=_job_id(raw),
            source_url=raw.get("jobUrl"),
            title=title,
            company=self.company.name,
            description=raw.get("descriptionPlain") or None,
            description_html=raw.get("descriptionHtml") or None,
            department=raw.get("department") or raw.get("team"),
            employment_type=_map_employment(raw.get("employmentType")),
            seniority=_guess_seniority(title),
            locations=[Location(raw=loc_name, remote=remote)] if loc_name else [],
            compensation=_map_compensation(raw.get("compensation")),
            posted_at=_parse_dt(raw.get("publishedAt")),
            apply_url=raw.get("applyUrl") or raw.get("jobUrl"),
            raw=raw,
        )


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _job_id(raw: dict) -> str:
    jid = raw.get("id")
    if jid:
        return str(jid)
    url = raw.get("jobUrl") or raw.get("applyUrl") or ""
    return url.rstrip("/").split("/")[-1] or raw["title"]


def _map_employment(value: Optional[str]) -> EmploymentType:
    if not value:
        return EmploymentType.other
    return _EMPLOYMENT_MAP.get(value.strip().lower(), EmploymentType.other)


def _map_compensation(comp: Optional[dict]) -> Optional[Compensation]:
    if not comp:
        return None
    summary = (
        comp.get("scrapeableCompensationSalarySummary")
        or comp.get("compensationTierSummary")
    )
    return Compensation(raw=summary) if summary else None


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
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