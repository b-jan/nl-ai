"""NotebookLM Enterprise Podcast API client.

Docs: https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/podcast-api

Standalone API — no notebook, no sources resource. Takes text contexts and
returns an MP3 via a long-running operation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx
from google.oauth2 import service_account
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
API_VERSION = "v1"
HOST = "https://discoveryengine.googleapis.com"


class PodcastAPIError(RuntimeError):
    pass


class PodcastTimeout(PodcastAPIError):
    pass


@dataclass
class PodcastResult:
    operation_name: str


class PodcastClient:
    """Minimal REST wrapper around the NotebookLM Enterprise Podcast API."""

    def __init__(
        self,
        *,
        project_id: str,
        service_account_info: dict[str, Any],
        client: httpx.Client | None = None,
    ) -> None:
        self.project_id = project_id
        self._creds = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES
        )
        self._client = client or httpx.Client(timeout=60.0)

    def _auth_header(self) -> dict[str, str]:
        from google.auth.transport.requests import Request

        if not self._creds.valid:
            self._creds.refresh(Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _parent(self) -> str:
        return f"projects/{self.project_id}/locations/global"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def create_podcast(
        self,
        *,
        title: str,
        contexts: Iterable[str],
        focus: str,
        language_code: str = "fr-FR",
        length: str = "SHORT",
        description: str | None = None,
    ) -> str:
        """Kick off podcast generation. Returns the LRO name."""
        context_list = [{"text": c} for c in contexts if c]
        if not context_list:
            raise PodcastAPIError("create_podcast called with empty contexts")
        body: dict[str, Any] = {
            "title": title,
            "podcastConfig": {
                "focus": focus,
                "length": length,
                "languageCode": language_code,
            },
            "contexts": context_list,
        }
        if description:
            body["description"] = description
        url = f"{HOST}/{API_VERSION}/{self._parent()}/podcasts"
        resp = self._client.post(
            url,
            json=body,
            headers={**self._auth_header(), "Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise PodcastAPIError(
                f"create_podcast failed: {resp.status_code} {resp.text}"
            )
        op = resp.json()
        op_name = op.get("name")
        if not op_name:
            raise PodcastAPIError(f"create_podcast: no operation name in {op}")
        logger.info("Started podcast op=%s", op_name)
        return op_name

    def poll_until_done(
        self,
        operation_name: str,
        *,
        timeout_s: int = 25 * 60,
        interval_s: int = 15,
    ) -> PodcastResult:
        deadline = time.monotonic() + timeout_s
        endpoint = f"{HOST}/{API_VERSION}/{operation_name}"
        while time.monotonic() < deadline:
            resp = self._client.get(endpoint, headers=self._auth_header())
            if resp.status_code >= 400:
                raise PodcastAPIError(f"poll failed: {resp.status_code} {resp.text}")
            data = resp.json()
            if data.get("done"):
                if "error" in data:
                    raise PodcastAPIError(f"operation error: {data['error']}")
                logger.info("Podcast ready: %s", operation_name)
                return PodcastResult(operation_name=operation_name)
            time.sleep(interval_s)
        raise PodcastTimeout(
            f"podcast operation {operation_name} not done after {timeout_s}s"
        )

    def download_audio(self, operation_name: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{HOST}/{API_VERSION}/{operation_name}:download"
        with self._client.stream(
            "GET",
            url,
            params={"alt": "media"},
            headers=self._auth_header(),
        ) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", errors="replace")
                raise PodcastAPIError(f"download failed: {resp.status_code} {body}")
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        logger.info("Downloaded audio to %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PodcastClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
