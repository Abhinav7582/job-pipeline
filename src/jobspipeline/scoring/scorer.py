"""
The scoring pipeline — the hybrid scorer, end to end.

    python -m jobspipeline.scoring.scorer            # score a capped batch
    python -m jobspipeline.scoring.scorer --limit 0  # score ALL survivors

Runs the profile's hard filters over every stored job, then LLM-scores each
survivor (Claude Haiku) for fit — weighing role, skills, domain, seniority, and
required experience vs the candidate's — writes Targets, and prints the ranked
shortlist.

Needs ANTHROPIC_API_KEY set (in .env or your shell).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from anthropic import Anthropic
from dotenv import load_dotenv

from ..core.storage import init_db, load_jobs, store_targets, top_targets
from ..schemas import Job
from ..targets import Target, TargetStatus
from .filters import hard_filter
from .profile import Profile, load_profile

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a precise job-fit scorer for one specific candidate.
Given the candidate profile and a job posting, rate how well the job fits the
candidate from 0 to 100, where 100 is an ideal fit.

Weigh, in rough priority: role/function match, required skills vs the candidate's,
domain overlap, seniority, and REQUIRED YEARS OF EXPERIENCE vs the candidate's.
A role that clearly requires substantially more experience than the candidate has
should score low even when skills match. Be discerning — most jobs are mediocre
fits, so use the full range and reserve 80+ for genuinely strong matches.

Respond with ONLY a JSON object and nothing else:
{"score": <integer 0-100>, "reasons": "<one sentence, 20 words max>"}"""


def _profile_text(p: Profile) -> str:
    return (
        f"Name: {p.name}\n"
        f"Years of experience: {p.years_experience}\n"
        f"Summary: {p.summary}\n"
        f"Target titles: {', '.join(p.target_titles)}\n"
        f"Skills: {', '.join(p.skills)}\n"
        f"Domains: {', '.join(p.domains)}\n"
        f"Nice to have: {', '.join(p.nice_to_haves)}"
    )


def _job_text(job: Job) -> str:
    loc = job.locations[0].raw if job.locations else "unspecified"
    desc = (job.description or "")[:2500]
    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {loc}\n"
        f"Seniority (rough guess): {job.seniority.value}\n\n"
        f"Description:\n{desc}"
    )


def _extract_json(text: str) -> dict:
    text = text.strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start : end + 1])


def score_job(client: Anthropic, profile: Profile, job: Job) -> tuple[int, str]:
    message = client.messages.create(
        model=MODEL,
        max_tokens=150,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"CANDIDATE PROFILE:\n{_profile_text(profile)}\n\n"
                       f"JOB POSTING:\n{_job_text(job)}",
        }],
    )
    text = "".join(b.text for b in message.content if b.type == "text")
    data = _extract_json(text)
    score = max(0, min(100, int(data["score"])))
    return score, str(data.get("reasons", ""))[:200]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25,
                        help="max survivors to LLM-score (0 = all)")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — add it to your .env file first.")
        return

    init_db()
    profile = load_profile()
    jobs = load_jobs()

    survivors = [j for j in jobs if hard_filter(j, profile).passed]
    to_score = survivors if args.limit == 0 else survivors[: args.limit]
    print(f"{len(survivors)} survivors; scoring {len(to_score)} with {MODEL}\u2026\n")

    client = Anthropic()
    targets: list[Target] = []
    for i, job in enumerate(to_score, 1):
        try:
            score, reasons = score_job(client, profile, job)
        except Exception as e:
            print(f"  !  {job.title[:40]:<40} scoring failed: {e}")
            continue
        targets.append(Target(
            job=job,
            status=TargetStatus.scored,
            score=score,
            score_reasons=reasons,
            scored_at=datetime.now(timezone.utc),
        ))
        print(f"  [{i}/{len(to_score)}]  {score:>3}  {job.title[:42]:<42} {job.company}")

    store_targets(targets)
    print(f"\nStored {len(targets)} scored targets.")

    print("\n=== Your top matches ===")
    for rec in top_targets(20):
        loc = rec.location or ""
        print(f"  {rec.score:>3}  {rec.title[:44]:<44} {rec.company[:18]:<18} {loc}")
        if rec.score_reasons:
            print(f"       {rec.score_reasons}")


if __name__ == "__main__":
    main()