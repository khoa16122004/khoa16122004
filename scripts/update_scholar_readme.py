from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


AUTHOR_ID = os.environ.get("SCHOLAR_USER_ID", "H8LRCN8AAAAJ")
LANG = os.environ.get("SCHOLAR_LANG", "en")
MAX_PAPERS = int(os.environ.get("SCHOLAR_MAX_PAPERS", "5"))
README_PATH = Path(os.environ.get("README_PATH", "README.md"))
CACHE_PATH = Path(os.environ.get("SCHOLAR_CACHE_PATH", "data/scholar-cache.json"))
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
START_MARKER = "<!-- SCHOLAR-PAPERS:START -->"
END_MARKER = "<!-- SCHOLAR-PAPERS:END -->"
STRICT_FETCH = os.environ.get("STRICT_SCHOLAR_FETCH", "false").lower() == "true"


@dataclass
class Paper:
    title: str
    link: str
    authors: str = ""
    venue: str = ""
    year: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "link": self.link,
            "authors": self.authors,
            "venue": self.venue,
            "year": self.year,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Paper":
        return cls(
            title=str(data.get("title", "")),
            link=str(data.get("link", "")),
            authors=str(data.get("authors", "")),
            venue=str(data.get("venue", "")),
            year=str(data.get("year", "")),
        )
def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def fetch_recent_papers() -> List[Paper]:
    if not SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY is not configured")

    query = urlencode(
        {
            "engine": "google_scholar_author",
            "author_id": AUTHOR_ID,
            "hl": LANG,
            "sort": "pubdate",
            "num": min(MAX_PAPERS, 100),
            "start": 0,
            "api_key": SERPAPI_API_KEY,
            "output": "json",
        }
    )
    url = f"{SERPAPI_ENDPOINT}?{query}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise RuntimeError(f"Unexpected content type: {content_type}")
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))

    papers = []
    for article in payload.get("articles", []):
        title = normalize_text(str(article.get("title", "")))
        link = str(article.get("link", "")).strip()
        if not title or not link:
            continue

        papers.append(
            Paper(
                title=title,
                link=link,
                authors=normalize_text(str(article.get("authors", ""))),
                venue=normalize_text(str(article.get("publication", ""))),
                year=normalize_text(str(article.get("year", ""))),
            )
        )

    if not papers:
        raise RuntimeError("No papers found in SerpAPI response")
    return papers[:MAX_PAPERS]


def write_cache(papers: List[Paper]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "author_id": AUTHOR_ID,
        "lang": LANG,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "papers": [paper.to_dict() for paper in papers],
    }
    CACHE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_cache() -> tuple[List[Paper], str | None]:
    if not CACHE_PATH.exists():
        return [], None

    payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    papers = [Paper.from_dict(item) for item in payload.get("papers", [])]
    papers = [paper for paper in papers if paper.title and paper.link][:MAX_PAPERS]
    return papers, payload.get("updated_at")


def render_section(papers: List[Paper], updated_at: str | None = None, source_label: str = "Google Scholar") -> str:
    lines = [
        START_MARKER,
        "## Latest Papers",
        "",
        f"<p>Source: <a href=\"https://scholar.google.com/citations?user={AUTHOR_ID}&hl={LANG}\">{source_label}</a></p>",
        "",
        "<table>",
        "  <thead>",
        "    <tr>",
        "      <th align=\"left\">Year</th>",
        "      <th align=\"left\">Publication</th>",
        "    </tr>",
        "  </thead>",
        "  <tbody>",
        "",
    ]
    for paper in papers:
        title = html.escape(paper.title)
        link = html.escape(paper.link, quote=True)
        authors = html.escape(paper.authors)
        venue = html.escape(paper.venue)
        year = html.escape(paper.year or "-")

        details = [f"<a href=\"{link}\"><strong>{title}</strong></a>"]
        if authors:
            details.append(f"<sub>{authors}</sub>")
        if venue:
            details.append(f"<br/><sub><em>{venue}</em></sub>")

        lines.extend(
            [
                "    <tr>",
                f"      <td valign=\"top\"><strong>{year}</strong></td>",
                f"      <td>{''.join(details)}</td>",
                "    </tr>",
            ]
        )
    lines.extend(["  </tbody>", "</table>"])
    if updated_at:
        lines.extend(["", f"<sub>Last successful sync: {html.escape(updated_at)}</sub>"])
    lines.extend(["", END_MARKER])
    return "\n".join(lines)


def update_readme(section: str) -> None:
    if not README_PATH.exists():
        raise FileNotFoundError(f"README not found: {README_PATH}")

    readme = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        re.DOTALL,
    )

    if pattern.search(readme):
        updated = pattern.sub(section, readme)
    else:
        suffix = "\n\n" if not readme.endswith("\n") else "\n"
        updated = f"{readme}{suffix}{section}\n"

    README_PATH.write_text(updated, encoding="utf-8")


def should_skip_fetch_error(exc: Exception) -> bool:
    if STRICT_FETCH:
        return False

    if isinstance(exc, HTTPError):
        return exc.code in {401, 403, 429}

    if isinstance(exc, RuntimeError):
        return "SERPAPI_API_KEY" in str(exc) or "searches limit" in str(exc).lower()

    return isinstance(exc, (URLError, TimeoutError))


def main() -> int:
    try:
        papers = fetch_recent_papers()
        write_cache(papers)
        cached_papers, updated_at = read_cache()
        update_readme(render_section(cached_papers, updated_at, "Google Scholar via SerpAPI"))
    except Exception as exc:
        if should_skip_fetch_error(exc):
            cached_papers, updated_at = read_cache()
            if cached_papers:
                update_readme(render_section(cached_papers, updated_at, "Google Scholar cache"))
                print(
                    f"Used cached Scholar data because SerpAPI fetch was unavailable: {exc}",
                    file=sys.stderr,
                )
                return 0

            print(
                f"Skipping README update because SerpAPI fetch was unavailable and no cache is available: {exc}",
                file=sys.stderr,
            )
            return 0

        print(f"Failed to update README from SerpAPI: {exc}", file=sys.stderr)
        return 1

    print(f"Updated {README_PATH} with {len(cached_papers)} papers from SerpAPI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())