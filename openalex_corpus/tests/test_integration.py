"""Explicitly opt in. One free singleton call, no content download or paid operation."""

import os

import pytest

from openalex_corpus.api.client import OpenAlexClient
from openalex_corpus.config import APISettings


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("OPENALEX_RUN_NETWORK") != "1", reason="Set OPENALEX_RUN_NETWORK=1 for live network test"
)
async def test_live_openalex_singleton(tmp_path):
    async with OpenAlexClient(APISettings(daily_request_budget=2), tmp_path) as client:
        work = await client.get_work("10.48550/arXiv.2205.01833")
    assert work["id"].startswith("https://openalex.org/W")
    assert "locations" in work
