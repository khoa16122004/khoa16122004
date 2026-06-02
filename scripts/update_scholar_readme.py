from __future__ import annotations

import html
import os
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AUTHOR_ID = os.environ.get("SCHOLAR_USER_ID", "H8LRCN8AAAAJ")
LANG = os.environ.get("SCHOLAR_LANG", "en")
MAX_PAPERS = int(os.environ.get("SCHOLAR_MAX_PAPERS", "5"))
README_PATH = Path(os.environ.get("README_PATH", "README.md"))
START_MARKER = "<!-- SCHOLAR-PAPERS:START -->"
END_MARKER = "<!-- SCHOLAR-PAPERS:END -->"


@dataclass
class Paper:
    title: str
    link: str
    authors: str = ""
    venue: str = ""
    year: str = ""


class ScholarHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.papers: List[Paper] = []
        self._in_row = False
        self._row_depth = 0
        self._capture_title = False
        self._capture_authors = False
        self._capture_venue = False
        self._capture_year = False
        self._meta_div_index = 0
        self._current_title: List[str] = []
        self._current_authors: List[str] = []
        self._current_venue: List[str] = []
        self._current_year: List[str] = []
        self._current_link = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")

        if tag == "tr" and "gsc_a_tr" in class_name:
            self._in_row = True
            self._row_depth = 1
            self._meta_div_index = 0
            self._current_title = []
            self._current_authors = []
            self._current_venue = []
            self._current_year = []
            self._current_link = ""
            return

        if not self._in_row:
            return

        if tag == "tr":
            self._row_depth += 1

        if tag == "a" and "gsc_a_at" in class_name:
            self._capture_title = True
            href = attrs_dict.get("href", "")
            self._current_link = f"https://scholar.google.com{href}" if href else ""
        elif tag == "div" and class_name == "gs_gray":
            if self._meta_div_index == 0:
                self._capture_authors = True
            elif self._meta_div_index == 1:
                self._capture_venue = True
            self._meta_div_index += 1
        elif tag == "span" and class_name == "gsc_a_h gsc_a_hc gs_ibl":
            self._capture_year = True
        elif tag == "td" and class_name == "gsc_a_y":
            self._capture_year = True

    def handle_endtag(self, tag: str) -> None:
        if self._capture_title and tag == "a":
            self._capture_title = False
        elif self._capture_authors and tag == "div":
            self._capture_authors = False
        elif self._capture_venue and tag == "div":
            self._capture_venue = False
        elif self._capture_year and tag in {"span", "td"}:
            self._capture_year = False

        if self._in_row and tag == "tr":
            self._row_depth -= 1
            if self._row_depth == 0:
                self._in_row = False
                title = normalize_text("".join(self._current_title))
                if title:
                    self.papers.append(
                        Paper(
                            title=title,
                            link=self._current_link,
                            authors=normalize_text("".join(self._current_authors)),
                            venue=normalize_text("".join(self._current_venue)),
                            year=normalize_text("".join(self._current_year)),
                        )
                    )

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title.append(data)
        elif self._capture_authors:
            self._current_authors.append(data)
        elif self._capture_venue:
            self._current_venue.append(data)
        elif self._capture_year:
            self._current_year.append(data)


def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def fetch_recent_papers() -> List[Paper]:
    url = (
        "https://scholar.google.com/citations"
        f"?user={AUTHOR_ID}&hl={LANG}&cstart=0&pagesize=100&sortby=pubdate"
    )
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=30) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            raise RuntimeError(f"Unexpected content type: {content_type}")
        html_text = response.read().decode("utf-8", errors="replace")

    parser = ScholarHTMLParser()
    parser.feed(html_text)
    papers = [paper for paper in parser.papers if paper.link]
    if not papers:
        raise RuntimeError("No papers found in Google Scholar HTML response")
    return papers[:MAX_PAPERS]


def render_section(papers: List[Paper]) -> str:
    lines = [
        START_MARKER,
        "## Latest Papers",
        "",
        f"Source: [Google Scholar](https://scholar.google.com/citations?user={AUTHOR_ID}&hl={LANG})",
        "",
    ]
    for paper in papers:
        meta_parts = [part for part in [paper.authors, paper.venue, paper.year] if part]
        meta = " | ".join(meta_parts)
        lines.append(f"- [{paper.title}]({paper.link})")
        if meta:
            lines.append(f"  - {meta}")
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


def main() -> int:
    try:
        papers = fetch_recent_papers()
        update_readme(render_section(papers))
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"Network error while fetching Google Scholar: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to update README from Google Scholar: {exc}", file=sys.stderr)
        return 1

    print(f"Updated {README_PATH} with {len(papers)} papers from Google Scholar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())