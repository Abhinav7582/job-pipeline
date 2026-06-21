"""
The runner — ties config + adapters + schema together.

    python -m jobpipeline.run

Loads data/companies.yaml, picks the right adapter for each company's ATS,
fetches every board, normalizes to Job, dedups across sources, and prints
a summary. This is the moment it stops being a schema and starts being a
pipeline.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schemas import Job
from .sources.base import CompanyConfig, SourceAdapter
from .sources.greenhouse import GreenhouseAdapter

# ATS name (from companies.yaml) -> adapter class. Add a line per new source.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    "greenhouse": GreenhouseAdapter,
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
            print(f"  ⚠  {company.name:<14} no adapter for '{company.ats}' — skipping")
            continue
        try:
            found = adapter_cls(company).fetch()
            print(f"  ✓  {company.name:<14} {len(found):>4} jobs")
            jobs.extend(found)
        except Exception as e:
            # One bad token shouldn't kill the whole run.
            print(f"  ✗  {company.name:<14} failed: {e}")
    return jobs


def dedup(jobs: list[Job]) -> list[Job]:
    """Collapse the same role seen from multiple sources via its dedup_key."""
    seen: dict[str, Job] = {}
    for job in jobs:
        seen.setdefault(job.dedup_key, job)
    return list(seen.values())


def main() -> None:
    print("Fetching boards…")
    jobs = fetch_all()
    unique = dedup(jobs)

    print(f"\nTotal: {len(jobs)} jobs  →  {len(unique)} after dedup")
    if unique:
        s = unique[0]
        loc = s.locations[0].raw if s.locations else "—"
        print("\nSample posting:")
        print(f"  {s.title}  @  {s.company}")
        print(f"  seniority: {s.seniority.value}   location: {loc}")
        print(f"  apply: {s.apply_url}")


if __name__ == "__main__":
    main()