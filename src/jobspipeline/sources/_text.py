"""
Shared text + parsing helpers used across source adapters.

These were duplicated in each adapter while the pattern settled; now that three
adapters share them, they live here in one place. Adapter-specific mapping
(Lever's commitment strings, Ashby's compensation object, etc.) stays in each
adapter where it belongs.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Optional

from ..schemas import RemoteType, Seniority

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(s: str) -> str:
    """Turn an HTML fragment into clean plain text."""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def guess_seniority(title: str) -> Seniority:
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


def guess_remote(loc: Optional[str]) -> RemoteType:
    if not loc:
        return RemoteType.unknown
    l = loc.lower()
    if "remote" in l:
        return RemoteType.remote
    if "hybrid" in l:
        return RemoteType.hybrid
    return RemoteType.on_site