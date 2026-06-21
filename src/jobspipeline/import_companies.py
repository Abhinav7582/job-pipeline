"""
Bulk importer for companies.yaml.

Feed it a file of careers-board URLs (one per line). It detects the ATS and
token from each URL, skips anything already in companies.yaml, and appends the
rest — so you can fatten the company list by pasting, not typing.

    python -m jobspipeline.import_companies my_list.txt
    python -m jobspipeline.import_companies my_list.txt --check

--check verifies each board actually returns jobs (using the adapters we have)
before adding it, so moved/empty boards like a dead Lever account get dropped
at import time instead of polluting your config.

Input lines are flexible — any of these work (text before the URL becomes the
display name; otherwise it's derived from the token):
    https://boards.greenhouse.io/stripe
    Stripe, https://boards.greenhouse.io/stripe
    Linear · ashby · https://jobs.ashbyhq.com/linear
Lines that are blank or start with '#' are ignored.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .sources.base import CompanyConfig

CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "companies.yaml"

_URL_RE = re.compile(r"https?://[^\s,|)\]]+")

# host substring -> ats name
_HOST_ATS = {
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "ashbyhq.com": "ashby",
    "workable.com": "workable",
    "recruitee.com": "recruitee",
}


def detect(url: str) -> "tuple[str, str] | None":
    """Return (ats, token) from a board URL, or None if unrecognized."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    for host_key, ats in _HOST_ATS.items():
        if host_key not in host:
            continue
        if ats == "recruitee":                      # <token>.recruitee.com
            token = host.split(".")[0]
            return (ats, token) if token else None
        if ats == "workable":                       # apply.workable.com/<token>
            if host.startswith("apply.") and path_parts:
                return ats, path_parts[0]
            sub = host.split(".")[0]                 # or <token>.workable.com
            return (ats, sub) if sub not in ("www", "apply") else None
        # greenhouse / lever / ashby: token is the first path segment
        if path_parts and path_parts[0] not in ("embed", "boards", "v1"):
            return ats, path_parts[0]
        return None
    return None


def _name_from(line: str, url: str, token: str) -> str:
    prefix = line.split(url)[0].strip(" ,|·-\t")
    if prefix:
        return prefix
    return token.replace("-", " ").replace("_", " ").title()


def parse_file(path: Path) -> list[CompanyConfig]:
    out: list[CompanyConfig] = []
    seen: set = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _URL_RE.search(line)
        if not match:
            continue
        url = match.group(0)
        detected = detect(url)
        if not detected:
            print(f"  ?  unrecognized: {url}")
            continue
        ats, token = detected
        key = (ats, token.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(CompanyConfig(name=_name_from(line, url, token), ats=ats, token=token))
    return out


def existing_keys() -> set:
    if not CONFIG_PATH.exists():
        return set()
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {(e["ats"], str(e["token"]).lower()) for e in (data.get("companies") or [])}


def _is_live(cfg: CompanyConfig) -> bool:
    # Reuse the real adapters to validate. We can only check ATSs we support;
    # anything else is kept as-is (it'll just skip at runtime until its adapter
    # exists).
    from .sources.greenhouse import GreenhouseAdapter
    from .sources.lever import LeverAdapter

    adapters = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter}
    cls = adapters.get(cfg.ats)
    if cls is None:
        return True
    try:
        return len(cls(cfg).fetch()) > 0
    except Exception:
        return False


def _yaml_str(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def append_to_yaml(configs: list[CompanyConfig]) -> None:
    if not configs:
        return
    blocks = [
        f"\n  - name: {_yaml_str(c.name)}\n    ats: {c.ats}\n    token: {c.token}"
        for c in configs
    ]
    with CONFIG_PATH.open("a") as f:
        f.write("".join(blocks) + "\n")


def main(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("--")]
    check = "--check" in argv
    if not args:
        print("usage: python -m jobspipeline.import_companies <file> [--check]")
        return

    in_path = Path(args[0])
    if not in_path.exists():
        print(f"file not found: {in_path}")
        return

    candidates = parse_file(in_path)
    have = existing_keys()
    new = [c for c in candidates if (c.ats, c.token.lower()) not in have]
    skipped = len(candidates) - len(new)

    if check and new:
        print("Checking boards are live…")
        live = []
        for c in new:
            ok = _is_live(c)
            mark = "\u2713" if ok else "\u2717"
            print(f"  {mark}  {c.name:<22} {c.ats}/{c.token}")
            if ok:
                live.append(c)
        new = live

    append_to_yaml(new)
    print(f"\nAdded {len(new)} companies, skipped {skipped} already present.")


if __name__ == "__main__":
    main(sys.argv[1:])