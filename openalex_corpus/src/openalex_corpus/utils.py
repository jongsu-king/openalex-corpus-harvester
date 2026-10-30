"""Durable atomic storage and secret-safe structured logs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_bytes(path: Path | str, content: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(tmp).unlink(missing_ok=True)


def atomic_text(path: Path | str, text: str):
    atomic_bytes(path, text.encode("utf-8"))


def atomic_json(path: Path | str, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n")


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if k.lower() not in {"api_key_present", "api_key_configured"}
            and any(s in k.lower() for s in ("api_key", "authorization", "token", "password"))
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        key = os.getenv("OPENALEX_API_KEY")
        if key:
            value = value.replace(key, "[REDACTED]")
        value = re.sub(r"(?i)(api_key|access_token|token)([=:]\s*)[^&\s\"']+", r"\1\2[REDACTED]", value)
        return value
    return value


class JSONFormatter(logging.Formatter):
    def format(self, record):
        data = {"time": utcnow(), "level": record.levelname, "message": record.getMessage()}
        data.update(getattr(record, "context", {}))
        return json.dumps(redact(data), ensure_ascii=False, default=str)


def configure_logging(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("openalex_corpus")
    logger.setLevel(logging.INFO)
    for old in logger.handlers[:]:
        old.close()
        logger.removeHandler(old)
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(log_dir / "corpus.jsonl", maxBytes=20_000_000, backupCount=5)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    # Never let HTTPX emit query URLs, which might contain credentials from a publisher.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return logger
