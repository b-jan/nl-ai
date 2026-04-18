"""Upload MP3 episodes as GitHub Release assets.

Public URL pattern (stable, served by GitHub's CDN via 302 redirect):
    https://github.com/{owner}/{repo}/releases/download/{tag}/{filename}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"


class ReleaseError(RuntimeError):
    pass


@dataclass
class ReleaseAsset:
    browser_download_url: str
    size: int
    tag: str


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _get_release_by_tag(
    client: httpx.Client, owner: str, repo: str, tag: str, token: str
) -> dict | None:
    resp = client.get(
        f"{API_ROOT}/repos/{owner}/{repo}/releases/tags/{tag}",
        headers=_headers(token),
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _create_release(
    client: httpx.Client,
    owner: str,
    repo: str,
    tag: str,
    title: str,
    body: str,
    token: str,
) -> dict:
    resp = client.post(
        f"{API_ROOT}/repos/{owner}/{repo}/releases",
        headers={**_headers(token), "Content-Type": "application/json"},
        json={
            "tag_name": tag,
            "name": title,
            "body": body,
            "draft": False,
            "prerelease": False,
        },
    )
    if resp.status_code >= 400:
        raise ReleaseError(
            f"create release failed: {resp.status_code} {resp.text}"
        )
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _upload_asset(
    client: httpx.Client,
    upload_url_template: str,
    filename: str,
    data: bytes,
    token: str,
) -> dict:
    # upload_url comes back as ".../assets{?name,label}" — strip the template.
    upload_url = upload_url_template.split("{", 1)[0]
    resp = client.post(
        upload_url,
        params={"name": filename},
        content=data,
        headers={
            **_headers(token),
            "Content-Type": "audio/mpeg",
        },
        timeout=300.0,
    )
    if resp.status_code >= 400:
        raise ReleaseError(
            f"asset upload failed: {resp.status_code} {resp.text}"
        )
    return resp.json()


def publish(
    mp3_path: Path,
    *,
    tag: str,
    title: str,
    body: str,
    filename: str,
    owner: str,
    repo: str,
    token: str,
    client: httpx.Client | None = None,
) -> ReleaseAsset:
    """Create (or reuse) a Release for ``tag`` and upload ``mp3_path`` as asset.

    Idempotent: if a release with the same tag already has an asset with the
    same filename, returns the existing URL.
    """
    owns = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        release = _get_release_by_tag(client, owner, repo, tag, token)
        if release is None:
            release = _create_release(client, owner, repo, tag, title, body, token)
            logger.info("Created release %s (id=%s)", tag, release.get("id"))
        else:
            logger.info("Reusing existing release %s (id=%s)", tag, release.get("id"))

        for existing in release.get("assets", []):
            if existing.get("name") == filename:
                logger.info("Asset %s already uploaded, reusing", filename)
                return ReleaseAsset(
                    browser_download_url=existing["browser_download_url"],
                    size=int(existing["size"]),
                    tag=tag,
                )

        data = mp3_path.read_bytes()
        asset = _upload_asset(
            client,
            release["upload_url"],
            filename,
            data,
            token,
        )
        return ReleaseAsset(
            browser_download_url=asset["browser_download_url"],
            size=int(asset["size"]),
            tag=tag,
        )
    finally:
        if owns:
            client.close()
