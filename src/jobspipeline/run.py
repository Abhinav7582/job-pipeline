"""
The runner — fetch every board, normalize, dedup, and persist.

    python -m jobpipeline.run

Loads data/companies.yaml, runs the right adapter per company, normalizes to
Job, dedups within the run, then stores to SQLite. The store reports how many
postings are NEW since the last run vs already known.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .core.storage import init_db, store_jobs
from .schemas import Job
from .sources.base import CompanyConfig, SourceAdapter
from .sources.greenhouse import GreenhouseAdapter
from .sources.lever import LeverAdapter
from .sources.ashby import AshbyAdapter

# ATS name (from companies.yaml) -> adapter class. Add a line per new source.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
}

# project root / data / companies.yaml  (run.py lives at src/jobpipeline/run.py)
CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "companies.yaml"


def load_companies() -> list[CompanyConfig]:
    data = yaml.safe_load(CONFIG_PATH.read_text())
    return [CompanyConfig(**entry) for entry in data["companies"]]


def fetch_all() -> list[Job]:
    jobs: list[Job] = []
    for company in load_companies():
        adapter_cls = ADAPTERS.get(company.ats)
        if adapter_cls is None:
            print(f"  \u26a0  {company.name:<14} no adapter for '{company.ats}' \u2014 skipping")
            continue
        try:
            found = adapter_cls(company).fetch()
            print(f"  \u2713  {company.name:<14} {len(found):>4} jobs")
            jobs.extend(found)
        except Exception as e:
            print(f"  \u2717  {company.name:<14} failed: {e}")
    return jobs


def dedup(jobs: list[Job]) -> list[Job]:
    """Collapse the same role seen multiple times within this run."""
    seen: dict[str, Job] = {}
    for job in jobs:
        seen.setdefault(job.dedup_key, job)
    return list(seen.values())


def main() -> None:
    init_db()

    print("Fetching boards\u2026")
    jobs = fetch_all()
    unique = dedup(jobs)
    result = store_jobs(unique)

    print(f"\nFetched {len(jobs)}  \u2192  {len(unique)} unique this run")
    print(
        f"DB: +{result.new} new, {result.seen} already known"
        f"  \u2192  {result.total} jobs stored total"
    )


if __name__ == "__main__":
    main()