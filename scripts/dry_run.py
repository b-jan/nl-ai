#!/usr/bin/env python3
"""Run the pipeline without committing anything.

Scrapes TLDR AI for ``--date`` (or today), generates the NotebookLM audio
overview, writes the MP3 to ``./out/``, and dumps the would-be RSS to stdout.
No GitHub Release, no git commit, no git push.

Requires the same env vars as the real run (GCP_* at minimum).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

from tldr_podcast import feed, state
from tldr_podcast.config import get_settings
from tldr_podcast.main import run_daily


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="ISO date YYYY-MM-DD", default=None)
    parser.add_argument(
        "--out",
        default="out",
        help="Directory where the MP3 is written (default: ./out)",
    )
    args = parser.parse_args()

    settings = get_settings()
    target_date = dt.date.fromisoformat(args.date) if args.date else None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"tldr-ai-{(target_date or dt.date.today()).isoformat()}.mp3"

    episode = run_daily(
        target_date,
        settings=settings,
        dry_run=True,
        audio_output=mp3_path,
    )
    if episode is None:
        print("No issue published today; nothing to do.", file=sys.stderr)
        return 0

    print(f"MP3 written to: {mp3_path}", file=sys.stderr)
    print(f"Duration: {episode.duration_seconds}s, size: {episode.audio_size_bytes} bytes",
          file=sys.stderr)

    # Render a preview RSS combining existing episodes + this one.
    existing = state.load_episodes(settings.episodes_json_path)
    preview = state.upsert_episode(existing, episode)
    rss_bytes = feed.build_feed(settings, preview)
    sys.stdout.buffer.write(rss_bytes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
