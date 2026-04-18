"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # GCP / Podcast API
    gcp_project_id: str = Field(..., alias="GCP_PROJECT_ID")
    gcp_sa_json_b64: str = Field(..., alias="GCP_SA_JSON")

    # GitHub
    github_token: str = Field(..., alias="GITHUB_TOKEN")
    github_owner: str = Field("b-jan", alias="GITHUB_OWNER")
    github_repo: str = Field("nl-ai", alias="GITHUB_REPO")
    github_pages_base_url: str = Field(
        "https://b-jan.github.io/nl-ai",
        alias="GITHUB_PAGES_BASE_URL",
    )

    # Podcast metadata
    podcast_title: str = Field("TLDR AI — Résumé quotidien", alias="PODCAST_TITLE")
    podcast_author: str = Field("TLDR AI FR", alias="PODCAST_AUTHOR")
    podcast_owner_email: str = Field(..., alias="PODCAST_OWNER_EMAIL")
    podcast_description: str = Field(
        "Résumé quotidien en français de la newsletter TLDR AI, généré automatiquement avec NotebookLM.",
        alias="PODCAST_DESCRIPTION",
    )
    podcast_language: str = Field("fr", alias="PODCAST_LANGUAGE")
    podcast_category: str = Field("Technology", alias="PODCAST_CATEGORY")

    # Layout
    repo_root: Path = Field(default_factory=lambda: Path.cwd(), alias="REPO_ROOT")

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_path(cls, v: object) -> Path:
        return Path(v) if not isinstance(v, Path) else v

    @property
    def service_account_info(self) -> dict:
        raw = self.gcp_sa_json_b64.strip()
        if raw.startswith("{"):
            return json.loads(raw)
        return json.loads(base64.b64decode(raw))

    @property
    def feed_dir(self) -> Path:
        return self.repo_root / "feed"

    @property
    def episodes_json_path(self) -> Path:
        return self.feed_dir / "episodes.json"

    @property
    def rss_xml_path(self) -> Path:
        return self.feed_dir / "rss.xml"

    @property
    def rss_public_url(self) -> str:
        return f"{self.github_pages_base_url.rstrip('/')}/rss.xml"

    @property
    def cover_public_url(self) -> str:
        return f"{self.github_pages_base_url.rstrip('/')}/cover.jpg"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
