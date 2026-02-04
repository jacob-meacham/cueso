"""Tests for the Brave Search API client."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.brave_search import BraveSearchClient, BraveSearchError, SearchResult
from tests.helpers import make_mock_response


@pytest.fixture
def brave_client(mock_http_client: AsyncMock) -> BraveSearchClient:
    return BraveSearchClient(api_key="test-key", http_client=mock_http_client)


class TestBraveSearchSuccess:
    @pytest.mark.asyncio
    async def test_returns_search_results(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(
            json_data={
                "web": {
                    "results": [
                        {
                            "title": "Result 1",
                            "url": "https://example.com/1",
                            "description": "First result",
                        },
                        {
                            "title": "Result 2",
                            "url": "https://example.com/2",
                            "description": "Second result",
                        },
                    ]
                }
            }
        )

        results = await brave_client.search("test query")

        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].title == "Result 1"
        assert results[0].url == "https://example.com/1"
        assert results[1].title == "Result 2"

    @pytest.mark.asyncio
    async def test_sends_correct_params(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(json_data={"web": {"results": []}})

        await brave_client.search("my query", count=5)

        mock_http_client.get.assert_called_once()
        call_kwargs = mock_http_client.get.call_args
        assert call_kwargs.kwargs["params"]["q"] == "my query"
        assert call_kwargs.kwargs["params"]["count"] == 5
        assert call_kwargs.kwargs["headers"]["X-Subscription-Token"] == "test-key"

    @pytest.mark.asyncio
    async def test_freshness_param(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(json_data={"web": {"results": []}})

        await brave_client.search("query", freshness="pw")

        call_kwargs = mock_http_client.get.call_args
        assert call_kwargs.kwargs["params"]["freshness"] == "pw"

    @pytest.mark.asyncio
    async def test_count_capped_at_20(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(json_data={"web": {"results": []}})

        await brave_client.search("query", count=50)

        call_kwargs = mock_http_client.get.call_args
        assert call_kwargs.kwargs["params"]["count"] == 20


class TestBraveSearchEmpty:
    @pytest.mark.asyncio
    async def test_empty_results(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(json_data={"web": {"results": []}})

        results = await brave_client.search("obscure query")
        assert results == []

    @pytest.mark.asyncio
    async def test_missing_web_key(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(json_data={})

        results = await brave_client.search("query")
        assert results == []


class TestBraveSearchErrors:
    @pytest.mark.asyncio
    async def test_http_401_error(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(status_code=401)

        with pytest.raises(BraveSearchError, match="401"):
            await brave_client.search("query")

    @pytest.mark.asyncio
    async def test_http_500_error(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.return_value = make_mock_response(status_code=500)

        with pytest.raises(BraveSearchError, match="500"):
            await brave_client.search("query")

    @pytest.mark.asyncio
    async def test_network_error(self, brave_client: BraveSearchClient, mock_http_client: AsyncMock) -> None:
        mock_http_client.get.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(BraveSearchError, match="request failed"):
            await brave_client.search("query")


class TestBraveSearchClientLifecycle:
    @pytest.mark.asyncio
    async def test_close_shared_client_does_not_close(self) -> None:
        shared_client = AsyncMock(spec=httpx.AsyncClient)
        client = BraveSearchClient(api_key="key", http_client=shared_client)

        await client.close()

        shared_client.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_owned_client_closes(self) -> None:
        client = BraveSearchClient(api_key="key")
        # Force creation of internal client
        internal = client._get_client()  # type: ignore[reportPrivateUsage]
        mock_close = AsyncMock()
        internal.aclose = mock_close  # type: ignore[method-assign]

        await client.close()

        mock_close.assert_called_once()
