"""
Greenhouse adapter — the first real source.

Greenhouse's job board API is public, unauthenticated, and returns an
entire board in one request:
    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

The `content=true` param includes each posting's description, which arrives
HTML-entity-encoded (e.g. "&lt;p&gt;"), so we unescape then strip tags to
get clean text for the scoring stage later.
"""

from __future__ import annotations

import html
from typing import Any, ClassVar

import httpx

from ..schemas import EmploymentType, Job, Location, SourceType
from ._text import guess_remote, guess_seniority, parse_iso_dt, strip_html
from .base import SourceAdapter

BOARD_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.greenhouse

    def fetch(self) -> list[Job]:
        url = BOARD_API.format(token=self.company.token)
        resp = httpx.get(url, params={"content": "true"}, timeout=30.0)
        resp.raise_for_status()          # a bad token -> 404 -> raised here
        payload = resp.json()
        return [self._to_job(raw) for raw in payload.get("jobs", [])]

    def _to_job(self, raw: dict[str, Any]) -> Job:
        unescaped_html = html.unescape(raw.get("content") or "")
        text = strip_html(unescaped_html)

        loc_name = (raw.get("location") or {}).get("name")
        departments = raw.get("departments") or []
        department = departments[0]["name"] if departments else None
        title = raw["title"]

        return Job(
            source=self.source_type,
            source_job_id=str(raw["id"]),
            source_url=raw.get("absolute_url"),
            title=title,
            company=self.company.name,
            description=text or None,
            description_html=unescaped_html or None,
            department=department,
            # The board API doesn't expose these reliably, so we infer from
            # the title. The scoring stage can refine later.
            employment_type=_guess_employment(title),
            seniority=guess_seniority(title),
            locations=(
                [Location(raw=loc_name, remote=guess_remote(loc_name))]
                if loc_name else []
            ),
            posted_at=parse_iso_dt(raw.get("first_published") or raw.get("updated_at")),
            updated_at=parse_iso_dt(raw.get("updated_at")),
            apply_url=raw.get("absolute_url"),
            raw=raw,
        )


def _guess_employment(title: str) -> EmploymentType:
    t = title.lower()
    if "intern" in t:
        return EmploymentType.internship
    if "contract" in t:
        return EmploymentType.contract
    return EmploymentType.full_time