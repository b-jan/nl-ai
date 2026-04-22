"""Scraper for the daily TLDR AI newsletter (https://tldr.tech/ai/YYYY-MM-DD)."""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

BASE_URL = "https://tldr.tech/ai"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# tldr.tech's edge returns 503 unless the request looks like a real browser
# navigation; the Upgrade-Insecure-Requests header is the minimum required.
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
}

# Headings / section names we never want to include in the summary.
SKIPPED_SECTIONS = {
    "sponsor",
    "sponsors",
    "jobs",
    "miscellaneous",  # kept optional: TLDR's "quick links" section
}


class IssueNotPublished(Exception):
    """Raised when the daily issue is not yet available (404)."""


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    summary: str


@dataclass(frozen=True)
class Issue:
    date: dt.date
    url: str
    articles: list[Article]

    def top_articles(self, limit: int = 6) -> list[Article]:
        return self.articles[:limit]


def _issue_url(date: dt.date) -> str:
    return f"{BASE_URL}/{date.isoformat()}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_like_sponsor(text: str) -> bool:
    lower = text.lower()
    return "(sponsor)" in lower or lower.endswith("sponsor")


def _iter_article_nodes(html: HTMLParser) -> Iterable[tuple[str, str, str]]:
    """Yield (title, url, summary) tuples from the issue page.

    TLDR issues render each article as an `<article>` block containing an
    `<h3>` (title + link) and a following paragraph (summary). The markup has
    shifted occasionally, so we fall back to any anchor whose href points at a
    tracking redirect (`/tracking/` prefix) or an external resource.
    """
    seen: set[str] = set()

    # Preferred path: explicit <article> nodes.
    for article in html.css("article"):
        link = article.css_first("a[href]")
        if link is None:
            continue
        href = link.attributes.get("href", "")
        title = _clean(link.text())
        if not href or not title or _looks_like_sponsor(title):
            continue

        current_section = _nearest_section_heading(article)
        if current_section and current_section.lower() in SKIPPED_SECTIONS:
            continue

        summary_node = article.css_first("div, p")
        summary = _clean(summary_node.text()) if summary_node else ""
        if href in seen:
            continue
        seen.add(href)
        yield title, href, summary

    if seen:
        return

    # Fallback: walk h3 > a pairs.
    for h3 in html.css("h3"):
        link = h3.css_first("a[href]")
        if link is None:
            continue
        title = _clean(link.text())
        href = link.attributes.get("href", "")
        if not title or not href or _looks_like_sponsor(title):
            continue
        nxt = h3.next
        summary = ""
        while nxt is not None and nxt.tag not in {"h3", "h2"}:
            if nxt.tag in {"p", "div"}:
                summary = _clean(nxt.text())
                if summary:
                    break
            nxt = nxt.next
        if href in seen:
            continue
        seen.add(href)
        yield title, href, summary


def _nearest_section_heading(node) -> str | None:
    parent = node.parent
    while parent is not None:
        heading = parent.css_first("h2")
        if heading is not None:
            return _clean(heading.text())
        parent = parent.parent
    return None


def fetch_issue(date: dt.date, client: httpx.Client | None = None) -> Issue:
    """Fetch and parse the TLDR AI issue for ``date``.

    Raises:
        IssueNotPublished: if the URL returns 404.
    """
    url = _issue_url(date)
    owns_client = client is None
    client = client or httpx.Client(
        timeout=20.0,
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
    )
    try:
        response = None
        for attempt in range(5):
            response = client.get(url)
            if response.status_code == 404:
                raise IssueNotPublished(
                    f"TLDR AI not published for {date.isoformat()}"
                )
            if response.status_code < 500:
                break
            delay = 2**attempt
            logger.warning(
                "tldr.tech returned %s for %s (attempt %d/5), retrying in %ds",
                response.status_code,
                url,
                attempt + 1,
                delay,
            )
            time.sleep(delay)
    finally:
        if owns_client:
            client.close()

    assert response is not None
    response.raise_for_status()

    html = HTMLParser(response.text)
    articles = [
        Article(title=t, url=u, summary=s) for (t, u, s) in _iter_article_nodes(html)
    ]

    logger.info("Parsed %d articles from %s", len(articles), url)
    return Issue(date=date, url=url, articles=articles)
