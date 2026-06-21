# job-pipeline

A multi-source job aggregator that pulls live postings from company career
boards, normalizes them into one schema, deduplicates across sources and across
runs, and stores them in a queryable database — built as the sourcing layer for
an automated, human-in-the-loop job-search pipeline.

> Status: sourcing layer complete. Scoring engine and web UI in progress.

## Why

Job hunting means watching dozens of company career pages, each on a different
applicant-tracking system (ATS), and re-checking them for new roles. This tool
collapses all of that into a single command: it fetches every tracked company's
board in parallel, turns wildly different API shapes into one consistent `Job`,
and tells you exactly what's *new since the last run*.

It deliberately sources from ATS platforms with clean public APIs rather than
scraping LinkedIn — legal, stable, and far less fragile.

## How it works

```
companies.yaml ─▶ adapters (Greenhouse │ Lever │ Ashby)
                       │  normalize every posting into one `Job`
                       ▼
                  dedup (by fingerprint)
                       ▼
                  SQLite store  ──▶  "+N new since last run"
```

The core idea is a **source-adapter pattern**: every source implements one
contract — `fetch() -> list[Job]` — so the rest of the system never knows or
cares where a posting came from. Adding a new ATS is one new file plus one line
in a registry; nothing downstream changes.

A second idea keeps the data clean: each `Job` carries a `dedup_key`
(a normalized fingerprint of company + title + location). It's the primary key
in the database, so storing is idempotent — the same role seen on two sources,
or the same board fetched twice, collapses to one row.

## Features

- **Three live ATS adapters** — Greenhouse, Lever, and Ashby, covering most
  tech, startup, and data-infrastructure roles.
- **Concurrent fetching** — all boards pulled in parallel via a thread pool.
- **Cross-run dedup** — a SQLite store keyed by fingerprint surfaces only what's
  genuinely new on each run.
- **Bulk importer** — turn a list of career-board URLs into config entries
  automatically, with ATS detection, dedup, and an optional live-check that
  drops dead boards before they're added.
- **One normalized schema** — a Pydantic `Job` that absorbs whatever each source
  offers (salary, employment type, remote status) without changing shape.

## Tech stack

Python · Pydantic v2 · SQLModel / SQLAlchemy · httpx · PyYAML

## Project structure

```
job-pipeline/
├── data/
│   └── companies.yaml          # company -> ATS -> board token
└── src/jobspipeline/
    ├── schemas.py              # the normalized Job (+ enums, dedup_key)
    ├── run.py                  # orchestrator: fetch -> dedup -> store
    ├── import_companies.py     # bulk URL importer
    ├── core/
    │   └── storage.py          # SQLite persistence layer
    └── sources/
        ├── base.py             # SourceAdapter contract + CompanyConfig
        ├── _text.py            # shared parsing helpers
        ├── greenhouse.py
        ├── lever.py
        └── ashby.py
```

## Setup

```bash
git clone https://github.com/Abhinav7582/job-pipeline.git
cd job-pipeline
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

## Usage

Fetch every board and store new postings:

```bash
python -m jobspipeline.run
```

```
Fetching boards…
  ✓  Stripe              512 jobs
  ✓  Databricks          758 jobs
  ...
Fetched 4120 jobs in 4.3s  →  4080 unique this run
DB: +4080 new, 0 already known  →  4080 jobs stored total
```

Add more companies from a list of board URLs:

```bash
python -m jobspipeline.import_companies my_list.txt --check
```

## Roadmap

- [ ] **Scoring engine** — rank each stored job against a profile so the output
      is a shortlist, not a firehose.
- [ ] **Web UI** — FastAPI + React, reading from the database.
- [ ] More sources (Workable, Recruitee; LinkedIn via job-alert email parsing).
- [ ] Tailored application/outreach drafting with a human approval step.