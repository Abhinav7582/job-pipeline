"""
Tailoring engine (Phase 3, step 1) — draft a tailored application for a role.

    python -m jobspipeline.outreach.tailor            # tailor your #1 top match
    python -m jobspipeline.outreach.tailor --rank 3   # tailor the 3rd-best match
    python -m jobspipeline.outreach.tailor --key <dedup_key>

Everything is grounded STRICTLY in data/resume.md — the model can only tailor
what's actually there; it is instructed never to invent experience, employers,
metrics, or skills. It writes a markdown draft to drafts/ for you to review,
edit, and use.

It does NOT send anything. Applying stays your decision — this just gets you from
a blank page to a strong draft in seconds.

Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from ..core.storage import load_jobs, top_targets
from ..scoring.profile import load_profile

load_dotenv()

# Sonnet, not Haiku: this is generative writing where quality matters and volume
# is low (one application at a time). Costs a few cents per draft.
MODEL = "claude-sonnet-5"

RESUME_PATH = Path(__file__).resolve().parents[3] / "data" / "resume.md"
DRAFTS_DIR = Path(__file__).resolve().parents[3] / "drafts"

SYSTEM_PROMPT = """You help {name} write a tailored job application. You are given
{name}'s full resume and one job posting.

Absolute rule: ground EVERYTHING strictly in the resume. Never invent experience,
employers, job titles, metrics, tools, or skills that aren't in the resume. If the
job wants something {name} lacks, don't fake it — lean on the closest real
strength instead. Every claim you make must be traceable to a resume line.

Match {name}'s real achievements to what THIS job needs. Be specific and concrete;
use the actual metrics from the resume. Avoid clichés and filler.

Output in exactly this markdown structure:

## Fit summary
2-3 sentences on why {name} is a strong fit for THIS specific role, citing real
resume facts.

## Highlights to emphasize
3-5 bullets naming the specific resume achievements/skills most relevant to this
job, each with its real metric.

## Cover letter
A concise, specific cover letter (~220-280 words) to the hiring team, grounded
only in resume facts. Open with genuine role/company relevance, not a template."""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "role"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rank", type=int, default=1, help="which top match to tailor (1 = best)")
    parser.add_argument("--key", type=str, default=None, help="tailor a specific target by dedup_key")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — add it to your .env file first.")
        return

    targets = top_targets(200)
    if not targets:
        print("No scored targets yet. Run the scorer first.")
        return

    if args.key:
        target = next((t for t in targets if t.dedup_key == args.key), None)
        if target is None:
            print(f"No scored target with key {args.key}.")
            return
    else:
        if args.rank < 1 or args.rank > len(targets):
            print(f"--rank must be between 1 and {len(targets)}.")
            return
        target = targets[args.rank - 1]

    resume = RESUME_PATH.read_text()
    name = load_profile().name
    jobs_by_key = {j.dedup_key: j for j in load_jobs()}
    job = jobs_by_key.get(target.dedup_key)
    description = (job.description if job else None) or "(no description available)"

    print(f"Tailoring for:  {target.title}  @  {target.company}  (score {target.score})")
    print(f"Writing with {MODEL}\u2026\n")

    client = Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=1600,
        system=SYSTEM_PROMPT.format(name=name),
        messages=[{
            "role": "user",
            "content": (
                f"RESUME:\n{resume}\n\n"
                f"JOB POSTING:\n"
                f"Title: {target.title}\n"
                f"Company: {target.company}\n"
                f"Location: {target.location or 'unspecified'}\n\n"
                f"Description:\n{description[:4000]}"
            ),
        }],
    )
    body = "".join(b.text for b in message.content if b.type == "text").strip()

    DRAFTS_DIR.mkdir(exist_ok=True)
    path = DRAFTS_DIR / f"{_slug(target.company)}--{_slug(target.title)}.md"
    header = (
        f"# {target.title} — {target.company}\n"
        f"Fit score: {target.score} · Location: {target.location or '—'}\n"
        f"Apply: {target.apply_url or '—'}\n\n---\n\n"
    )
    path.write_text(header + body + "\n")

    print(body)
    print(f"\nDraft saved: {path}")
    print("Review and edit it, then apply yourself via the link above.")


if __name__ == "__main__":
    main()