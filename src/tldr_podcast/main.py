"""End-to-end orchestration: scrape TLDR → NotebookLM → Release → RSS → git push."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from mutagen.mp3 import MP3

from . import feed, release, state, tldr_scraper
from .config import Settings, get_settings
from .notebooklm import NotebookLMClient
from .tldr_scraper import Article, IssueNotPublished

logger = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


MIN_ARTICLES = 3
TOP_N = 6


def _today_paris() -> dt.date:
    # Routine scheduler runs in Europe/Paris, but default to UTC if tz missing.
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return dt.datetime.utcnow().date()


def _build_summary(articles: list[Article]) -> str:
    lines = ["Au sommaire de cet épisode :", ""]
    for a in articles:
        bullet = f"• {a.title}"
        if a.summary:
            bullet += f" — {a.summary[:180]}"
        lines.append(bullet)
    return "\n".join(lines)


def _episode_focus(articles: list[Article]) -> str:
    titles = "; ".join(a.title for a in articles)
    return (
        "Podcast en français, format dialogue 2 voix façon NotebookLM, ton naturel "
        "et dynamique. Résume les articles suivants en environ 6 minutes, en "
        "mettant en perspective les enjeux IA : "
        f"{titles}"
    )


def _mp3_metadata(path: Path) -> tuple[int, int]:
    audio = MP3(path)
    duration = int(audio.info.length) if audio.info and audio.info.length else 0
    return duration, path.stat().st_size


def _git(args: list[str], cwd: Path) -> None:
    logger.info("git %s", " ".join(args))
    subprocess.run(["git", *args], cwd=cwd, check=True)


def _commit_and_push(settings: Settings, date: dt.date) -> None:
    repo = settings.repo_root
    _git(["add", "feed/"], repo)
    # If nothing staged, skip commit.
    status = subprocess.run(
        ["git", "status", "--porcelain", "feed/"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        logger.info("No feed changes to commit")
        return

    _git(
        [
            "-c",
            "user.email=bot@tldr-ai-fr.local",
            "-c",
            "user.name=TLDR AI FR bot",
            "commit",
            "-m",
            f"Épisode {date.isoformat()}",
        ],
        repo,
    )
    _git(["push", "origin", "HEAD"], repo)


def run_daily(
    date: dt.date | None = None,
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    audio_output: Path | None = None,
) -> state.Episode | None:
    """Execute the full daily pipeline. Returns the newly created Episode
    or ``None`` if the issue wasn't published (weekend / US holiday).
    """
    settings = settings or get_settings()
    date = date or _today_paris()
    logger.info("Running daily pipeline for %s (dry_run=%s)", date, dry_run)

    # 1. Scrape
    try:
        issue = tldr_scraper.fetch_issue(date)
    except IssueNotPublished:
        logger.warning("TLDR AI issue not published for %s, exiting cleanly", date)
        return None

    articles = issue.top_articles(limit=TOP_N)
    if len(articles) < MIN_ARTICLES:
        raise PipelineError(
            f"Only {len(articles)} articles extracted for {date} (<{MIN_ARTICLES})"
        )

    episodes = state.load_episodes(settings.episodes_json_path)
    if state.already_published(episodes, date) and not dry_run:
        logger.info("Episode for %s already published, skipping", date)
        return None

    summary = _build_summary(articles)

    # 2. NotebookLM: generate French audio overview
    with NotebookLMClient(
        project_id=settings.gcp_project_id,
        location=settings.gcp_location,
        service_account_info=settings.service_account_info,
        impersonated_user=settings.podcast_owner_email,
    ) as nlm:
        notebook_id = nlm.create_notebook(f"TLDR AI {date.isoformat()} FR")
        nlm.add_web_sources(notebook_id, [a.url for a in articles])
        op = nlm.start_audio_overview(
            notebook_id,
            language_code="fr-FR",
            length="SHORT",
            episode_focus=_episode_focus(articles),
        )
        result = nlm.poll_until_done(op)

        mp3_path = audio_output or Path(tempfile.gettempdir()) / f"tldr-ai-{date}.mp3"
        nlm.download_audio(result.audio_uri, mp3_path)

    duration_s, size_bytes = _mp3_metadata(mp3_path)
    episode_title = f"TLDR AI — {date.strftime('%d/%m/%Y')}"

    if dry_run:
        logger.info("Dry run: skipping release + feed + commit. MP3 at %s", mp3_path)
        return state.Episode(
            date=date.isoformat(),
            title=episode_title,
            summary=summary,
            audio_url=f"file://{mp3_path}",
            audio_size_bytes=size_bytes,
            duration_seconds=duration_s,
            published_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            source_urls=[a.url for a in articles],
        )

    # 3. Upload MP3 as GitHub Release asset
    filename = f"tldr-ai-fr-{date.isoformat()}.mp3"
    asset = release.publish(
        mp3_path,
        tag=f"ep-{date.isoformat()}",
        title=episode_title,
        body=summary,
        filename=filename,
        owner=settings.github_owner,
        repo=settings.github_repo,
        token=settings.github_token,
    )

    # 4. Update state + regenerate RSS
    episode = state.Episode(
        date=date.isoformat(),
        title=episode_title,
        summary=summary,
        audio_url=asset.browser_download_url,
        audio_size_bytes=asset.size,
        duration_seconds=duration_s,
        published_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_urls=[a.url for a in articles],
    )
    episodes = state.upsert_episode(episodes, episode)
    state.save_episodes(settings.episodes_json_path, episodes)
    feed.rebuild(settings, episodes)

    # 5. Commit + push
    _commit_and_push(settings, date)
    return episode


def cli() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="tldr-podcast")
    parser.add_argument("--date", help="ISO date YYYY-MM-DD (default: today in Europe/Paris)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_date = dt.date.fromisoformat(args.date) if args.date else None
    try:
        run_daily(target_date, dry_run=args.dry_run)
    except IssueNotPublished:
        return 0
    except Exception:
        logger.exception("Pipeline failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
