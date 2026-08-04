"""
LinkedIn job-alert email adapter.

You can't legally scrape LinkedIn's site — but you CAN read your own inbox. Set
up LinkedIn job alerts for your searches (e.g. "Data Analyst, Bengaluru"), and
LinkedIn emails you matching roles. This adapter connects to your mailbox over
IMAP, finds those alert emails, and pulls each job card into a normalized `Job`.

This reaches employers that aren't on Greenhouse/Lever/Ashby — which is most big
Indian companies — with zero ban risk, because it never touches LinkedIn's site.

Config (in .env):
    IMAP_HOST=imap.gmail.com
    IMAP_USER=you@gmail.com
    IMAP_PASSWORD=your-app-password      # a Gmail App Password, not your login

NOTE: LinkedIn's email templates change over time, so the company/location
parsing is best-effort. Titles and apply links are reliable; if company or
location comes out blank on your real emails, the _parse_jobs heuristics are the
place to tune (share a sample and it's a quick fix).
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Optional

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from ..schemas import EmploymentType, Job, Location, SourceType
from ._text import guess_remote, guess_seniority
from .base import SourceAdapter

load_dotenv()

_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")
_BOILERPLATE = {
    "view job", "view jobs", "see all jobs", "actively recruiting",
    "easy apply", "promoted", "be an early applicant", "apply", "save",
}


def email_configured() -> bool:
    return all(os.getenv(k) for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"))


class LinkedInEmailAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.linkedin_email

    def __init__(self, since_days: int = 7, folder: str = "INBOX") -> None:
        # Not company-driven like the ATS adapters, so no CompanyConfig.
        self.since_days = since_days
        self.folder = folder

    def fetch(self) -> list[Job]:
        htmls = self._fetch_alert_emails()
        by_id: dict[str, Job] = {}
        for html in htmls:
            for raw in _parse_jobs(html):
                by_id[raw["job_id"]] = self._to_job(raw)  # last one wins
        return list(by_id.values())

    # --- email retrieval ---------------------------------------------------- #

    def _fetch_alert_emails(self) -> list[str]:
        host = os.environ["IMAP_HOST"]
        user = os.environ["IMAP_USER"]
        password = os.environ["IMAP_PASSWORD"]
        since = (datetime.now(timezone.utc) - timedelta(days=self.since_days)).strftime("%d-%b-%Y")

        htmls: list[str] = []
        box = imaplib.IMAP4_SSL(host)
        try:
            box.login(user, password)
            box.select(self.folder)
            # any mail from linkedin in the window; non-job mails just parse empty
            typ, data = box.search(None, f'(FROM "linkedin" SINCE {since})')
            for eid in data[0].split():
                typ, msg_data = box.fetch(eid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                html = _get_html_part(msg)
                if html:
                    htmls.append(html)
        finally:
            try:
                box.logout()
            except Exception:
                pass
        return htmls

    # --- mapping ------------------------------------------------------------ #

    def _to_job(self, raw: dict) -> Job:
        loc_name = raw.get("location")
        return Job(
            source=self.source_type,
            source_job_id=raw["job_id"],
            source_url=raw["url"],
            title=raw["title"],
            company=raw.get("company") or "Unknown",
            # LinkedIn alert emails carry no full description; the apply link
            # goes to the full posting on LinkedIn.
            description=None,
            employment_type=EmploymentType.other,
            seniority=guess_seniority(raw["title"]),
            locations=[Location(raw=loc_name, remote=guess_remote(loc_name))] if loc_name else [],
            apply_url=raw["url"],
            raw=raw,
        )


# --------------------------------------------------------------------------- #
# Parsing helpers                                                               #
# --------------------------------------------------------------------------- #

def _get_html_part(msg: email.message.Message) -> Optional[str]:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return None
    if msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", "replace")
    return None


def _job_id(href: str) -> Optional[str]:
    m = _JOB_ID_RE.search(href)
    return m.group(1) if m else None


def _parse_jobs(html: str) -> list[dict]:
    """Best-effort: pull one record per job card out of an alert email."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        jid = _job_id(a["href"])
        if not jid:
            continue
        title = a.get_text(" ", strip=True)
        if not title or title.lower() in _BOILERPLATE:
            continue

        # walk up to the card: nearest ancestor that also holds a /company/ link
        company = None
        card = a
        for _ in range(6):
            card = card.parent
            if card is None:
                break
            comp = card.find("a", href=lambda h: h and "/company/" in h)
            if comp:
                company = comp.get_text(" ", strip=True) or None
                break

        location = _guess_location(card, title, company) if card is not None else None

        rec = {
            "job_id": jid,
            "title": title,
            "company": company,
            "location": location,
            "url": f"https://www.linkedin.com/jobs/view/{jid}/",
        }
        # keep the richest title per job id
        if jid not in out or len(title) > len(out[jid]["title"]):
            out[jid] = rec
    return list(out.values())


def _guess_location(card, title: Optional[str], company: Optional[str]) -> Optional[str]:
    skip = {s.lower() for s in _BOILERPLATE}
    if title:
        skip.add(title.lower())
    if company:
        skip.add(company.lower())
    for text in card.stripped_strings:
        t = text.strip()
        low = t.lower()
        if low in skip or len(t) < 3 or len(t) > 60:
            continue
        # a location usually has a comma (City, Region) or a remote hint
        if "," in t or "remote" in low or "hybrid" in low or "on-site" in low:
            return t
    return None