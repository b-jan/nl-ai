"""Build an iTunes / Spotify-compliant podcast RSS feed."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from feedgen.feed import FeedGenerator

from .config import Settings
from .state import Episode


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_feed(settings: Settings, episodes: list[Episode]) -> bytes:
    fg = FeedGenerator()
    fg.load_extension("podcast")

    fg.title(settings.podcast_title)
    fg.link(href=settings.rss_public_url, rel="self")
    fg.link(href=settings.github_pages_base_url, rel="alternate")
    fg.description(settings.podcast_description)
    fg.language(settings.podcast_language)
    fg.author({"name": settings.podcast_author, "email": settings.podcast_owner_email})
    fg.image(settings.cover_public_url)

    fg.podcast.itunes_author(settings.podcast_author)
    fg.podcast.itunes_summary(settings.podcast_description)
    fg.podcast.itunes_category(settings.podcast_category)
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_owner(
        name=settings.podcast_author, email=settings.podcast_owner_email
    )
    fg.podcast.itunes_image(settings.cover_public_url)
    fg.podcast.itunes_type("episodic")

    # Episodes are appended oldest-first so newest ends up at the top of the feed.
    for episode in sorted(episodes, key=lambda e: e.date):
        fe = fg.add_entry()
        fe.id(episode.guid)
        fe.guid(episode.guid, permalink=False)
        fe.title(episode.title)
        fe.description(episode.summary)
        fe.link(href=episode.audio_url)
        fe.enclosure(
            url=episode.audio_url,
            length=str(episode.audio_size_bytes),
            type="audio/mpeg",
        )
        published_at = _parse_rfc3339(episode.published_at)
        fe.pubDate(published_at)
        fe.podcast.itunes_duration(_fmt_duration(episode.duration_seconds))
        fe.podcast.itunes_explicit("no")

    return fg.rss_str(pretty=True)


def _parse_rfc3339(value: str) -> dt.datetime:
    # Python's fromisoformat in 3.12 handles "Z" suffix.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = dt.datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=dt.timezone.utc)
    return result


def rebuild(settings: Settings, episodes: list[Episode], target: Path | None = None) -> Path:
    target = target or settings.rss_xml_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_feed(settings, episodes))
    return target
