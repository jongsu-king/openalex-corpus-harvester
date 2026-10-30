"""Offline API fixtures: no network traffic or metered downloads."""

import asyncio
import json

import httpx
import pytest

from openalex_corpus.api.client import APIError, BudgetExceeded, BudgetJournal, OpenAlexClient
from openalex_corpus.api.pagination import next_cursor
from openalex_corpus.api.search import SearchSpec
from openalex_corpus.config import APISettings


def settings(**overrides):
    return APISettings(requests_per_second=30, max_attempts=2, **overrides)


def test_normal_response_and_full_unknown_fields_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "secret-never-in-url")
    raw = {"id": "https://openalex.org/W1", "unknown_extension": {"nested": [1, None, ""]}}

    def handler(request):
        assert request.headers["Authorization"] == "Bearer secret-never-in-url"
        assert "api_key" not in request.url.params
        assert request.url.params["per_page"] == "100"
        return httpx.Response(200, json={"meta": {"count": 1, "next_cursor": None}, "results": [raw]})

    async def run():
        async with OpenAlexClient(settings(), tmp_path, httpx.MockTransport(handler)) as client:
            page = await client.get_page(SearchSpec(query="solar cell").params())
            assert page["results"][0] == raw

    asyncio.run(run())
    assert "secret-never-in-url" not in (tmp_path / "network_budget.json").read_text()


def test_empty_search(tmp_path):
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"meta": {"count": 0, "next_cursor": None}, "results": []}
            )
        )
        async with OpenAlexClient(settings(), tmp_path, transport) as client:
            page = await client.get_page({})
            assert page["results"] == []
            assert next_cursor(page, "*") is None

    asyncio.run(run())


def test_cursor_pagination_and_loop_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "test")
    received = []

    def handler(request):
        cursor = request.url.params["cursor"]
        received.append(cursor)
        return httpx.Response(
            200,
            json={
                "meta": {"count": 2, "next_cursor": "next" if cursor == "*" else None},
                "results": [
                    {"id": "https://openalex.org/W1" if cursor == "*" else "https://openalex.org/W2"}
                ],
            },
        )

    async def run():
        async with OpenAlexClient(settings(), tmp_path, httpx.MockTransport(handler)) as client:
            page = await client.get_page({})
            await client.get_page({}, cursor=next_cursor(page, "*"))

    asyncio.run(run())
    assert received == ["*", "next"]
    with pytest.raises(APIError, match="repeated"):
        next_cursor({"meta": {"next_cursor": "same"}, "results": [{"id": "W1"}]}, "same")


def test_anonymous_cursor_fails_before_request(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)

    async def run():
        async with OpenAlexClient(
            settings(), tmp_path, httpx.MockTransport(lambda r: pytest.fail("No request allowed"))
        ) as client:
            with pytest.raises(APIError) as error:
                await client.get_page({}, "next")
            assert error.value.code == "API_KEY_REQUIRED"

    asyncio.run(run())


def test_429_retry_then_success(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"id": "https://openalex.org/W1"})

    async def run():
        async with OpenAlexClient(settings(), tmp_path, httpx.MockTransport(handler)) as client:
            assert (await client.get_work("W1"))["id"].endswith("W1")

    asyncio.run(run())
    assert len(calls) == 2


def test_timeout_is_bounded_and_does_not_leak_url(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("https://example.org/?api_key=should-not-leak", request=request)

    async def run():
        async with OpenAlexClient(settings(), tmp_path, httpx.MockTransport(handler)) as client:
            with pytest.raises(APIError) as error:
                await client.get_work("W1")
            assert error.value.code == "API_TIMEOUT"
            assert error.value.retryable
            assert "should-not-leak" not in str(error.value)

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_key_error_is_not_retried(tmp_path, status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"message": "server might echo api_key=do-not-log"})

    async def run():
        async with OpenAlexClient(settings(), tmp_path, httpx.MockTransport(handler)) as client:
            with pytest.raises(APIError) as error:
                await client.get_work("W1")
            assert error.value.code == "API_AUTH_ERROR"
            assert not error.value.retryable
            assert "do-not-log" not in str(error.value)

    asyncio.run(run())
    assert len(calls) == 1


def test_durable_budget_cannot_be_reset_by_new_client(tmp_path):
    async def run():
        response = httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "https://openalex.org/W1"}))
        async with OpenAlexClient(settings(daily_request_budget=1), tmp_path, response) as client:
            await client.get_work("W1")
        async with OpenAlexClient(settings(daily_request_budget=1), tmp_path, response) as client:
            with pytest.raises(BudgetExceeded):
                await client.get_work("W2")

    asyncio.run(run())
    assert json.loads((tmp_path / "network_budget.json").read_text())["metadata_requests"] == 1


def test_rate_limit_exhaustion_and_headers_persist(tmp_path):
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Credits-Used": "10", "X-RateLimit-Reset": "3600"}

    async def run():
        async with OpenAlexClient(
            settings(), tmp_path, httpx.MockTransport(lambda r: httpx.Response(429, headers=headers))
        ) as client:
            with pytest.raises(BudgetExceeded) as error:
                await client.get_page({"search": "test"})
            assert error.value.retry_at

    asyncio.run(run())
    journal = json.loads((tmp_path / "network_budget.json").read_text())
    assert journal["blocked_until"]
    assert journal["rate_headers"]["x-ratelimit-remaining"] == "0"


def test_search_filter_validation_and_combinations():
    params = SearchSpec(
        query="organic solar",
        title="polymer",
        abstract="donor",
        from_year=2018,
        to_year=2026,
        author="A123",
        institution="I1",
        source="S2",
        topic="T3",
        oa=True,
        has_pdf=True,
        has_content=True,
        min_citations=4,
    ).params()
    assert params["per_page"] == 100
    assert params["search.title"] == "polymer"
    for expected in [
        "publication_year:2018-2026",
        "has_content.pdf:true",
        "cited_by_count:>3",
        "authorships.author.id:A123",
    ]:
        assert expected in params["filter"]
    with pytest.raises(ValueError, match="Unsupported"):
        SearchSpec(filters={"invented.filter": True})
    with pytest.raises(ValueError, match="requires an OpenAlex"):
        SearchSpec(author="John Smith")


def test_budget_corruption_fails_closed(tmp_path):
    (tmp_path / "network_budget.json").write_text("not json")
    with pytest.raises(APIError, match="unreadable"):
        BudgetJournal(tmp_path).reserve_metadata(0.01, 10, 1)


def test_malformed_page_not_silently_skipped(tmp_path):
    async def run():
        async with OpenAlexClient(
            settings(),
            tmp_path,
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={"results": [{"title": "missing id"}], "meta": {"count": 1, "next_cursor": None}},
                )
            ),
        ) as client:
            with pytest.raises(APIError, match="without an ID"):
                await client.get_page({})

    asyncio.run(run())


def test_direct_usd_cost_header_takes_precedence(tmp_path):
    journal = BudgetJournal(tmp_path)
    journal.reserve_metadata(0.001, 100, 1)
    journal.observe_headers(
        httpx.Headers({"X-RateLimit-Cost-USD": "0.002", "X-RateLimit-Credits-Used": "1"}), 0.001
    )
    data = json.loads((tmp_path / "network_budget.json").read_text())
    assert float(data["metadata_reserved_usd"]) == 0.002
