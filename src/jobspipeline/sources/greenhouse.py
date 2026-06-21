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
import re
from datetime import datetime
from typing import Any, ClassVar, Optional

import httpx

from ..schemas import (
    EmploymentType,
    Job,
    Location,
    RemoteType,
    Seniority,
    SourceType,
)
from .base import SourceAdapter

BOARD_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.greenhouse

    def fetch(self) -> list[Job]:
        url = BOARD_API.format(token=self.company.token)
        resp = httpx.get(url, params={"content": "true"}, timeout=30.0)
        resp.raise_for_status()              # a bad token -> 404 -> raised here
        payload = resp.json()
        return [self._to_job(raw) for raw in payload.get("jobs", [])]

    def _to_job(self, raw: dict[str, Any]) -> Job:
        unescaped_html = html.unescape(raw.get("content") or "")
        text = _strip_html(unescaped_html)

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
            seniority=_guess_seniority(title),
            locations=(
                [Location(raw=loc_name, remote=_guess_remote(loc_name))]
                if loc_name else []
            ),
            posted_at=_parse_dt(raw.get("first_published") or raw.get("updated_at")),
            updated_at=_parse_dt(raw.get("updated_at")),
            apply_url=raw.get("absolute_url"),
            raw=raw,
        )


# --------------------------------------------------------------------------- #
# Small parsing helpers                                                         #
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)          # catch any entities left inside text nodes
    return _WS_RE.sub(" ", s).strip()


def _guess_employment(title: str) -> EmploymentType:
    t = title.lower()
    if "intern" in t:
        return EmploymentType.internship
    if "contract" in t:
        return EmploymentType.contract
    return EmploymentType.full_time


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


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None