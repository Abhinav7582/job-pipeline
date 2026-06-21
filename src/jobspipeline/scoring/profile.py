"""
The master profile — what the scoring engine measures every job against.

It serves both stages of the hybrid scorer:
  - the HARD-FILTER fields (seniority, locations, remote_ok, employment_types,
    dealbreaker_keywords) cheaply cut the firehose down before any LLM call
  - the SOFT fields (summary, skills, domains, nice_to_haves) give the LLM the
    nuance it needs to score and explain the survivors

Edit data/profile.yaml to change any of this — no code changes needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from ..schemas import EmploymentType, RemoteType, Seniority

PROFILE_PATH = Path(__file__).resolve().parents[3] / "data" / "profile.yaml"


class Profile(BaseModel):
    # --- identity + freeform (the LLM scorer reads these for nuance) ---
    name: str
    summary: str                       # 2-4 sentences: who you are + ideal role
    target_titles: list[str]
    skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    nice_to_haves: list[str] = Field(default_factory=list)

    # --- hard filters (applied before the expensive LLM scoring) ---
    seniority: list[Seniority] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)        # substring match
    remote_ok: list[RemoteType] = Field(default_factory=list)
    employment_types: list[EmploymentType] = Field(
        default_factory=lambda: [EmploymentType.full_time]
    )
    dealbreaker_keywords: list[str] = Field(default_factory=list)
    min_salary: Optional[int] = None
    currency: Optional[str] = None


def load_profile(path: Path = PROFILE_PATH) -> Profile:
    data = yaml.safe_load(path.read_text())
    return Profile(**data)


if __name__ == "__main__":
    p = load_profile()
    print(f"Loaded profile for {p.name}")
    print(f"  targets:   {', '.join(p.target_titles)}")
    print(f"  seniority: {[s.value for s in p.seniority]}")
    print(f"  remote ok: {[r.value for r in p.remote_ok]}")
    print(f"  skills:    {len(p.skills)} listed")
    print(f"  dealbreakers: {p.dealbreaker_keywords}")