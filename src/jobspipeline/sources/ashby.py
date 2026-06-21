"""
Ashby adapter — third ATS, identical contract.

    GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true

Ashby has the cleanest compensation data of the public ATS feeds. Quirks handled:
an invalid/empty board returns {"jobs": []} (not a 404), and the feed includes
unlisted/draft roles, so we keep only isListed=true. Generic title/date helpers
come from _text.
"""

from __future__ import annotations

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
from ._text import guess_remote, guess_seniority, parse_iso_dt
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
            remote = RemoteType.remote if raw.get("isRemote") else guess_remote(loc_name)

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
            seniority=guess_seniority(title),
            locations=[Location(raw=loc_name, remote=remote)] if loc_name else [],
            compensation=_map_compensation(raw.get("compensation")),
            posted_at=parse_iso_dt(raw.get("publishedAt")),
            apply_url=raw.get("applyUrl") or raw.get("jobUrl"),
            raw=raw,
        )


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