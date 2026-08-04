"""
The runner — fetch every source concurrently, normalize, dedup, and persist.

    python -m jobspipeline.run

Loads data/companies.yaml, runs the right adapter per company (in parallel),
then adds LinkedIn job-alert emails if IMAP is configured, dedups within the
run, and stores to SQLite — reporting how many postings are NEW since the last
run vs already known.
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
from .sources.linkedin_email import LinkedInEmailAdapter, email_configured

# ATS name (from companies.yaml) -> adapter class. Add a line per new source.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
}

# project root / data / companies.yaml  (run.py lives at src/jobspipeline/run.py)
CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "companies.yaml"

MAX_WORKERS = 12


def load_companies() -> list[CompanyConfig]:
    data = yaml.safe_load(CONFIG_PATH.read_text())
    return [CompanyConfig(**entry) for entry in data["companies"]]


def _fetch_one(company: CompanyConfig):
    adapter_cls = ADAPTERS.get(company.ats)
    if adapter_cls is None:
        return company, None, f"no adapter for '{company.ats}'"
    try:
        return company, adapter_cls(company).fetch(), None
    except Exception as e:
        return company, None, str(e)


def fetch_all() -> list[Job]:
    jobs: list[Job] = []

    # 1) ATS boards, concurrently
    companies = load_companies()
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

    # 2) LinkedIn job-alert emails, if configured
    if email_configured():
        try:
            found = LinkedInEmailAdapter().fetch()
            print(f"  \u2713  {'LinkedIn alerts':<18} {len(found):>4} jobs")
            jobs.extend(found)
        except Exception as e:
            print(f"  \u2717  {'LinkedIn alerts':<18} failed: {e}")

    return jobs


def dedup(jobs: list[Job]) -> list[Job]:
    seen: dict[str, Job] = {}
    for job in jobs:
        seen.setdefault(job.dedup_key, job)
    return list(seen.values())


def main() -> None:
    init_db()
    print("Fetching sources\u2026")
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