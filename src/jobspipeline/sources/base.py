"""
The source-adapter contract.

This is the single rule the whole pipeline relies on: *every* source —
a Greenhouse board, a Lever board, a Telegram channel, a LinkedIn alert
inbox — is some subclass of `SourceAdapter` whose `fetch()` returns a
list of normalized `Job` objects. Nothing downstream (scoring, dedup,
the DB) ever knows or cares where a job actually came from.

Add a new source = write one new subclass. That's the entire point of
the pattern: the core system never changes, only the edges grow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from ..schemas import Job, SourceType


class CompanyConfig(BaseModel):
    """One row from data/companies.yaml, validated."""
    name: str          # display name, e.g. "Stripe"
    ats: str           # which adapter handles it, e.g. "greenhouse"
    token: str         # the source-specific identifier (board slug, etc.)


class SourceAdapter(ABC):
    """Base class every source implements."""

    # Each subclass declares which SourceType it produces.
    source_type: ClassVar[SourceType]

    def __init__(self, company: CompanyConfig) -> None:
        self.company = company

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return all current postings from this source as normalized Jobs."""
        raise NotImplementedError