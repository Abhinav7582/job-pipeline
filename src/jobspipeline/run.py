"""
The runner — fetch every board concurrently, normalize, dedup, and persist.

    python -m jobspipeline.run

Loads data/companies.yaml, runs the right adapter per company (in parallel),
normalizes to Job, dedups within the run, then stores to SQLite. The store
reports how many postings are NEW since the last run vs already known.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from .core.storage import init_db, store_jobs
from .schemas import Job
from .sources.ashby import AshbyAdapter
from .sources.base import CompanyConfig, SourceAdapter
from .sources.greenhouse import GreenhouseAdapter
from .sources.lever import LeverAdapter

# ATS name (from companies.yaml) -> adapter class. Add a line per new source.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
}

# project root / data / companies.yaml  (run.py lives at src/jobspipeline/run.py)
CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "companies.yaml"

# How many boards to fetch at once. The work is I/O-bound (waiting on HTTP), so
# threads are plenty; kept modest to stay friendly to Ashby's ~100 req/min limit.
MAX_WORKERS = 12


def load_companies() -> list[CompanyConfig]:
    data = yaml.safe_load(CONFIG_PATH.read_text())
    return [CompanyConfig(**entry) for entry in data["companies"]]


def _fetch_one(company: CompanyConfig):
    """Fetch one company's board. Returns (company, jobs|None, error|None)."""
    adapter_cls = ADAPTERS.get(company.ats)
    if adapter_cls is None:
        return company, None, f"no adapter for '{company.ats}'"
    try:
        return company, adapter_cls(company).fetch(), None
    except Exception as e:
        return company, None, str(e)


def fetch_all() -> list[Job]:
    companies = load_companies()
    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_fetch_one, c) for c in companies]
        for future in as_completed(futures):
            company, found, error = future.result()
            if found is not None:
                print(f"  \u2713  {company.name:<18} {len(found):>4} jobs")
                jobs.extend(found)
            elif error and "no adapter" in error:
                print(f"  \u26a0  {company.name:<18} {error} \u2014 skipping")
            else:
                print(f"  \u2717  {company.name:<18} failed: {error}")
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
    start = time.perf_counter()
    jobs = fetch_all()
    elapsed = time.perf_counter() - start

    unique = dedup(jobs)
    result = store_jobs(unique)

    print(f"\nFetched {len(jobs)} jobs in {elapsed:.1f}s  \u2192  {len(unique)} unique this run")
    print(
        f"DB: +{result.new} new, {result.seen} already known"
        f"  \u2192  {result.total} jobs stored total"
    )


if __name__ == "__main__":
    main()