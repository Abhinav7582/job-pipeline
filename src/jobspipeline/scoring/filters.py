"""
Hard filters — the cheap first pass of the hybrid scorer.

Before spending an LLM call on a job, we drop the obvious non-matches using the
profile's filter fields. Each failed filter returns a reason, so the funnel is
explainable ("dropped 5,692 for location").

Design decisions baked in here:
  - RELEVANCE first: the title must look like a target role (role_keywords), or
    it's dropped. This is a positive gate — keep known-good rather than chase an
    endless list of known-bad titles.
  - seniority "unknown" PASSES — our title-based guess is crude, so let the LLM
    judge rather than dropping a real match we couldn't classify
  - dealbreakers match the TITLE only
  - location passes if it matches a profile location OR the job is remote-ok
  - missing data passes — don't drop a job for something we don't know
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..schemas import EmploymentType, Job, RemoteType, Seniority
from .profile import Profile


@dataclass
class FilterResult:
    passed: bool
    reason: Optional[str] = None       # why it failed; None if it passed


def hard_filter(job: Job, profile: Profile) -> FilterResult:
    title_l = job.title.lower()

    # 1) relevance — title must look like a target role
    if profile.role_keywords:
        if not any(kw.lower() in title_l for kw in profile.role_keywords):
            return FilterResult(False, "off-function")

    # 2) dealbreakers — title only
    for kw in profile.dealbreaker_keywords:
        if kw.lower() in title_l:
            return FilterResult(False, f"dealbreaker: {kw}")

    # 3) seniority — unknown passes
    if profile.seniority and job.seniority != Seniority.unknown:
        if job.seniority not in profile.seniority:
            return FilterResult(False, f"seniority: {job.seniority.value}")

    # 4) employment type — 'other' passes (sources often don't specify)
    if profile.employment_types and job.employment_type != EmploymentType.other:
        if job.employment_type not in profile.employment_types:
            return FilterResult(False, f"employment: {job.employment_type.value}")

    # 5) location — match a profile location OR be remote (if remote is ok)
    if profile.locations:
        remote_ok = RemoteType.remote in profile.remote_ok
        is_remote = any(loc.remote == RemoteType.remote for loc in job.locations)
        loc_text = " ".join((loc.raw or loc.city or "") for loc in job.locations).lower()
        has_location = bool(loc_text.strip())
        matches = any(p.lower() in loc_text for p in profile.locations)
        if has_location and not matches and not (remote_ok and is_remote):
            return FilterResult(False, "location")

    # 6) salary floor — only when both sides have a number
    if profile.min_salary and job.compensation and job.compensation.max_amount:
        if job.compensation.max_amount < profile.min_salary:
            return FilterResult(False, "below salary floor")

    return FilterResult(True)