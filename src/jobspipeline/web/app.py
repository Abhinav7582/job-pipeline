"""
A tiny web UI for browsing your scored job shortlist.

    python -m jobspipeline.web.app
    # then open http://localhost:8000

One FastAPI app serves both the dashboard page and a single JSON endpoint that
reads scored targets straight from jobs.db — ranked by fit score, each with a
direct link to apply. No build step, no separate frontend server.
"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from ..core.storage import top_targets

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"

app = FastAPI(title="Job Pipeline")


@app.get("/api/targets")
def api_targets() -> JSONResponse:
    """Every scored target, ranked by fit score (highest first)."""
    rows = top_targets(10_000)
    data = [
        {
            "score": r.score,
            "title": r.title,
            "company": r.company,
            "location": r.location,
            "source": r.source,
            "apply_url": r.apply_url,
            "reasons": r.score_reasons,
        }
        for r in rows
    ]
    return JSONResponse(data)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX)


def main() -> None:
    print("Job shortlist UI  ->  http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()