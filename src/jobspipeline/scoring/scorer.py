"""
The scoring pipeline — the hybrid scorer, end to end, INCREMENTAL.

    uv run python -m jobspipeline.scoring.scorer            # score only NEW roles (cap 25)
    uv run python -m jobspipeline.scoring.scorer --limit 0  # score ALL new roles
    uv run python -m jobspipeline.scoring.scorer --rescore --limit 0  # re-score everything

Runs the profile's hard filters over every stored job, then LLM-scores only the
survivors that haven't been scored yet. Writes Targets and prints the ranked
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

from ..core.storage import init_db, load_jobs, scored_keys, store_targets, top_targets
from ..schemas import Job
from ..targets import Target, TargetStatus
from .filters import hard_filter
from .profile import Profile, load_profile

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"

# {years} and {years_plus} are filled per-run from the profile.
SYSTEM_PROMPT = """You are a precise job-fit scorer for one specific candidate.
Score how well each job fits the candidate from 0 to 100.

EXPERIENCE RULE (important):
The candidate has {years} years of experience and performs at a strong, senior
level for that tenure (billion-scale pipelines, real ownership — see the profile).
Treat any role asking for up to about {years_plus} years as a FULL match on
experience; do NOT lower the score for it, and do NOT reflexively penalize a role
just because its title says "Senior". Only apply an experience penalty when a role
clearly requires substantially more (roughly 6+ years) or is a genuine
people-management / team-leadership role.

FIT RUBRIC — use the FULL range and differentiate finely:
  85-100  exceptional: function, tech stack, and domain all align; experience in range
  70-84   strong: clearly a good fit with at most one minor gap
  50-69   partial: relevant but with real gaps in domain, stack, or level
  30-49   weak: some overlap but mostly off-target
  0-29    poor: wrong function or wrong domain
Do NOT cluster many roles on the same round number. If two roles differ in fit,
give them different scores — vary within a band (e.g. 83, 79, 76, 71), never snap
everything to 78 or 72.

WEIGH, in priority: function/role match; tech-stack overlap (SQL, Python, PySpark,
dbt, Airflow, Databricks, Snowflake); domain overlap (AdTech, data infrastructure,
product/revenue analytics are the strongest); then experience per the rule above.

Respond with ONLY a JSON object and nothing else:
{{"score": <integer 0-100>, "reasons": "<one sentence, 20 words max>"}}"""


def _system(profile: Profile) -> str:
    return SYSTEM_PROMPT.format(
        years=profile.years_experience,
        years_plus=profile.years_experience + 2,
    )


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
        f"Description:\n{desc or '(no description available)'}"
    )


def _extract_json(text: str) -> dict:
    text = text.strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start : end + 1])


def score_job(client: Anthropic, system: str, profile: Profile, job: Job) -> tuple[int, str]:
    message = client.messages.create(
        model=MODEL,
        max_tokens=150,
        system=system,
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
    parser.add_argument("--limit", type=int, default=25, help="max NEW survivors to score (0 = all)")
    parser.add_argument("--rescore", action="store_true", help="re-score every survivor, even already-scored")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — add it to your .env file first.")
        return

    init_db()
    profile = load_profile()
    jobs = load_jobs()

    survivors = [j for j in jobs if hard_filter(j, profile).passed]
    already = set() if args.rescore else scored_keys()
    pending = [j for j in survivors if j.dedup_key not in already]
    skipped = len(survivors) - len(pending)

    to_score = pending if args.limit == 0 else pending[: args.limit]
    print(f"{len(survivors)} survivors  ·  {skipped} already scored  ·  {len(pending)} new to score")
    if to_score:
        print(f"Scoring {len(to_score)} with {MODEL}\u2026\n")

    targets: list[Target] = []
    if to_score:
        client = Anthropic()
        system = _system(profile)
        for i, job in enumerate(to_score, 1):
            try:
                score, reasons = score_job(client, system, profile, job)
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

    if targets:
        store_targets(targets)
        print(f"\nStored {len(targets)} newly scored targets.")
    else:
        print("\nNothing new to score — your shortlist is up to date.")

    print("\n=== Your top matches ===")
    for rec in top_targets(20):
        loc = rec.location or ""
        print(f"  {rec.score:>3}  {rec.title[:44]:<44} {rec.company[:18]:<18} {loc}")
        if rec.score_reasons:
            print(f"       {rec.score_reasons}")


if __name__ == "__main__":
    main()