"""Persistent state: versioned list of published episodes."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Episode:
    date: str               # ISO date "YYYY-MM-DD"
    title: str
    summary: str
    audio_url: str
    audio_size_bytes: int
    duration_seconds: int
    published_at: str       # RFC 3339 UTC
    source_urls: list[str] = field(default_factory=list)

    @property
    def guid(self) -> str:
        return self.audio_url


def load_episodes(path: Path) -> list[Episode]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text() or "[]")
    return [Episode(**item) for item in raw]


def save_episodes(path: Path, episodes: list[Episode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialisable = [asdict(e) for e in episodes]
    path.write_text(json.dumps(serialisable, ensure_ascii=False, indent=2) + "\n")


def already_published(episodes: list[Episode], date: dt.date) -> bool:
    iso = date.isoformat()
    return any(e.date == iso for e in episodes)


def upsert_episode(episodes: list[Episode], episode: Episode) -> list[Episode]:
    """Insert ``episode`` or replace an existing entry with the same date.

    Episodes are kept sorted by date descending (newest first) for determinism.
    """
    without = [e for e in episodes if e.date != episode.date]
    without.append(episode)
    without.sort(key=lambda e: e.date, reverse=True)
    return without
