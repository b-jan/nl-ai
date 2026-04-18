"""NotebookLM Enterprise API client.

Docs: https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-audio-overview
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
API_VERSION = "v1alpha"


def _host_for_location(location: str) -> str:
    return f"https://{location}-discoveryengine.googleapis.com"


class NotebookLMError(RuntimeError):
    pass


class AudioOverviewTimeout(NotebookLMError):
    pass


@dataclass
class AudioOverviewResult:
    audio_uri: str
    operation_name: str


class NotebookLMClient:
    """Minimal REST wrapper around NotebookLM Enterprise."""

    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        service_account_info: dict[str, Any],
        impersonated_user: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.project_id = project_id
        self.location = location
        self._creds = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES, subject=impersonated_user
        )
        self._client = client or httpx.Client(timeout=60.0)

    # ----- auth -----
    def _auth_header(self) -> dict[str, str]:
        from google.auth.transport.requests import Request

        if not self._creds.valid:
            self._creds.refresh(Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _host(self) -> str:
        return _host_for_location(self.location)

    def _parent(self) -> str:
        return (
            f"{self._host()}/{API_VERSION}/projects/{self.project_id}"
            f"/locations/{self.location}"
        )

    # ----- notebooks -----
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def create_notebook(self, title: str) -> str:
        url = f"{self._parent()}/notebooks"
        resp = self._client.post(
            url,
            json={"title": title},
            headers={**self._auth_header(), "Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise NotebookLMError(
                f"create_notebook failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        name = data.get("name", "")
        notebook_id = name.rsplit("/", 1)[-1] if "/" in name else data.get("notebookId")
        if not notebook_id:
            raise NotebookLMError(f"create_notebook: cannot extract id from {data}")
        logger.info("Created notebook %s", notebook_id)
        return notebook_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def add_web_sources(self, notebook_id: str, urls: Iterable[str]) -> None:
        url_list = list(urls)
        if not url_list:
            raise NotebookLMError("add_web_sources called with empty URL list")
        endpoint = f"{self._parent()}/notebooks/{notebook_id}/sources:batchCreate"
        body = {
            "userContents": [
                {"webContent": {"url": u}} for u in url_list
            ]
        }
        resp = self._client.post(
            endpoint,
            json=body,
            headers={**self._auth_header(), "Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise NotebookLMError(
                f"add_web_sources failed: {resp.status_code} {resp.text}"
            )
        logger.info("Added %d sources to notebook %s", len(url_list), notebook_id)

    def start_audio_overview(
        self,
        notebook_id: str,
        *,
        language_code: str = "fr-FR",
        length: str = "SHORT",
        episode_focus: str | None = None,
    ) -> str:
        endpoint = f"{self._parent()}/notebooks/{notebook_id}/audioOverviews:create"
        body: dict[str, Any] = {"languageCode": language_code, "length": length}
        if episode_focus:
            body["episodeFocus"] = episode_focus
        resp = self._client.post(
            endpoint,
            json=body,
            headers={**self._auth_header(), "Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            raise NotebookLMError(
                f"start_audio_overview failed: {resp.status_code} {resp.text}"
            )
        op = resp.json()
        op_name = op.get("name")
        if not op_name:
            raise NotebookLMError(f"start_audio_overview: no operation name in {op}")
        logger.info("Started audio overview op=%s", op_name)
        return op_name

    def poll_until_done(
        self,
        operation_name: str,
        *,
        timeout_s: int = 25 * 60,
        interval_s: int = 15,
    ) -> AudioOverviewResult:
        deadline = time.monotonic() + timeout_s
        endpoint = f"{self._host()}/{API_VERSION}/{operation_name}"
        while time.monotonic() < deadline:
            resp = self._client.get(endpoint, headers=self._auth_header())
            if resp.status_code >= 400:
                raise NotebookLMError(
                    f"poll failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()
            if data.get("done"):
                if "error" in data:
                    raise NotebookLMError(f"operation error: {data['error']}")
                response = data.get("response", {})
                audio = response.get("audioOverview") or response
                uri = audio.get("audioUri") or audio.get("audio_uri")
                if not uri:
                    raise NotebookLMError(f"no audioUri in response: {data}")
                logger.info("Audio overview ready: %s", uri)
                return AudioOverviewResult(audio_uri=uri, operation_name=operation_name)
            time.sleep(interval_s)
        raise AudioOverviewTimeout(
            f"audio overview {operation_name} not done after {timeout_s}s"
        )

    def download_audio(self, audio_uri: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream(
            "GET", audio_uri, headers=self._auth_header()
        ) as resp:
            if resp.status_code >= 400:
                raise NotebookLMError(
                    f"download failed: {resp.status_code} {resp.text}"
                )
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        logger.info("Downloaded audio to %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NotebookLMClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
