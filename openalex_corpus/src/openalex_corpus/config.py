"""Validated YAML configuration; secrets are deliberately not configuration fields."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class APISettings(StrictModel):
    base_url: str = "https://api.openalex.org"
    timeout: float = Field(45, gt=0)
    max_attempts: int = Field(4, ge=1, le=10)
    requests_per_second: float = Field(2, gt=0, le=30)
    daily_request_budget: int = Field(1000, ge=0)
    daily_budget_usd: float = Field(0.05, ge=0)
    estimated_list_cost_usd: float = Field(0.0001, ge=0)
    estimated_search_cost_usd: float = Field(0.001, ge=0)
    max_retry_after_seconds: float = Field(30, ge=0, le=60)


class DownloadSettings(StrictModel):
    refresh_existing: bool = False
    timeout: float = Field(60, gt=0)
    max_attempts: int = Field(3, ge=1, le=10)
    concurrency: int = Field(3, ge=1, le=16)
    max_downloads: int = Field(100, ge=0)
    max_file_bytes: int = Field(100 * 1024 * 1024, ge=1024)
    max_content_downloads: int = Field(0, ge=0)
    max_content_cost_usd: float = Field(0, ge=0)
    content_cost_usd: float = Field(0.01, gt=0)
    allowed_licenses: list[str] | None = None
    user_agent: str = "OpenAlexCorpusHarvester/0.1 (local scholarly archiver)"
    max_retry_after_seconds: float = Field(30, ge=0, le=60)


class ParsingSettings(StrictModel):
    primary_engine: Literal["docling", "pymupdf"] = "docling"
    enable_docling: bool = True
    enable_ocr: bool = False
    grobid_url: str | None = None
    grobid_timeout: float = Field(120, gt=0)
    ocr_timeout: float = Field(300, gt=0)
    render_dpi: int = Field(110, ge=50, le=300)
    text_min_chars: int = Field(20, ge=0)
    docling_artifacts_path: str | None = None
    allow_model_downloads: bool = False
    tei_path: str | None = None
    docling_timeout: float = Field(300, gt=0)
    docling_threads: int = Field(2, ge=1, le=16)
    workers: int = Field(1, ge=1, le=8)
    memory_limit_mb: int = Field(4096, ge=256)
    worker_memory_mb: int = Field(2048, ge=128)
    timeout_seconds: float = Field(600, gt=0)
    max_recovery_attempts: int = Field(2, ge=0, le=5)
    render_all_pages: bool = True

    @model_validator(mode="after")
    def check_workers(self):
        if self.workers * self.worker_memory_mb > self.memory_limit_mb:
            raise ValueError("workers * worker_memory_mb must fit memory_limit_mb")
        return self


class Settings(StrictModel):
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    api: APISettings = Field(default_factory=APISettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    parsing: ParsingSettings = Field(default_factory=ParsingSettings)
    search: dict = Field(default_factory=dict)
    max_works: int = Field(100, ge=1, le=10000)
    retry_max_attempts: int = Field(3, ge=1, le=10)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "database" / "corpus.sqlite"

    def prepare(self):
        self.data_dir = self.data_dir.resolve()
        self.log_dir = self.log_dir.resolve()
        for path in [
            self.data_dir,
            self.db_path.parent,
            self.data_dir / "papers",
            self.data_dir / "exports",
            self.data_dir / "temporary",
            self.data_dir / "state",
            self.data_dir / "crawls",
            self.log_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)
        return self


def load_settings(path: Path | None = None) -> Settings:
    # Relative paths are based on the YAML's project root for config/config.yaml,
    # otherwise on the YAML directory. This works when invoked from any cwd.
    values = (yaml.safe_load(path.read_text()) or {}) if path else {}
    base = (
        path.resolve().parent.parent
        if path and path.parent.name == "config"
        else path.resolve().parent
        if path
        else Path.cwd()
    )
    for key in ("data_dir", "log_dir"):
        override = os.environ.get("OPENALEX_CORPUS_" + key.upper())
        value = Path(override or values.get(key, "data" if key == "data_dir" else "logs"))
        values[key] = value if value.is_absolute() else base / value
    for key in ("docling_artifacts_path", "tei_path"):
        configured = values.get("parsing", {}).get(key)
        if configured:
            candidate = Path(configured)
            values["parsing"][key] = str(candidate if candidate.is_absolute() else base / candidate)
    return Settings.model_validate(values)
