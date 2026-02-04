"""Tests for the content search and Roku launch pipeline."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.brave_search import BraveSearchError, SearchResult
from app.core.search_and_play import build_search_query, launch_on_roku, search_content
from app.core.streaming import HULU, NETFLIX

ROKU_BASE_URL = "http://192.168.1.100:8060"


class TestBuildSearchQuery:
    def test_title_only(self) -> None:
        assert build_search_query("The Bear") == "The Bear"

    def test_title_and_season(self) -> None:
        assert build_search_query("The Bear", season=3) == "The Bear Season 3"

    def test_full_episode(self) -> None:
        result = build_search_query("Rick and Morty", season=4, episode=5, episode_title="Rattlestar Ricklactica")
        assert result == "Rick and Morty Season 4 Episode 5 Rattlestar Ricklactica"

    def test_title_and_episode_title(self) -> None:
        result = build_search_query("Rick and Morty", episode_title="Rattlestar Ricklactica")
        assert result == "Rick and Morty Rattlestar Ricklactica"


class TestSearchContent:
    @pytest.mark.asyncio
    async def test_single_match(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(
                title="The Bear | Netflix",
                url="https://www.netflix.com/title/81231974",
                description="Watch The Bear on Netflix",
            )
        ]

        result = await search_content(
            title="The Bear",
            season=3,
            episode=10,
            brave_client=mock_brave_client,
        )

        assert result.success is True
        assert len(result.matches) == 1
        assert result.matches[0].service_name == "netflix"
        assert result.matches[0].content_id == "81231974"
        assert result.matches[0].channel_id == NETFLIX.roku_channel_id

    @pytest.mark.asyncio
    async def test_multiple_service_matches(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(
                title="The Bear | Netflix",
                url="https://www.netflix.com/title/81231974",
                description="Watch on Netflix",
            ),
            SearchResult(
                title="The Bear | Hulu",
                url="https://www.hulu.com/series/the-bear-565d8976-9e52-4f30-a6f5-a47e7fe1abd4",
                description="Watch on Hulu",
            ),
        ]

        result = await search_content(title="The Bear", brave_client=mock_brave_client, services=[NETFLIX, HULU])

        assert result.success is True
        assert len(result.matches) == 2
        assert result.matches[0].service_name == "netflix"
        assert result.matches[1].service_name == "hulu"

    @pytest.mark.asyncio
    async def test_deduplicates_by_service(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="First Netflix", url="https://www.netflix.com/title/111", description=""),
            SearchResult(title="Second Netflix", url="https://www.netflix.com/title/222", description=""),
            SearchResult(
                title="Hulu",
                url="https://www.hulu.com/series/abc-12345678-1234-1234-1234-123456789abc",
                description="",
            ),
        ]

        result = await search_content(title="Show", brave_client=mock_brave_client, services=[NETFLIX, HULU])

        assert result.success is True
        assert len(result.matches) == 2
        # First Netflix URL wins
        assert result.matches[0].content_id == "111"
        assert result.matches[1].service_name == "hulu"

    @pytest.mark.asyncio
    async def test_media_type_override(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="Show", url="https://www.netflix.com/title/12345", description=""),
        ]

        result = await search_content(title="Show", brave_client=mock_brave_client, media_type="episode")

        assert result.success is True
        assert result.matches[0].media_type == "episode"

    @pytest.mark.asyncio
    async def test_skips_non_matching_urls(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="IMDB", url="https://www.imdb.com/title/tt123/", description=""),
            SearchResult(title="Wikipedia", url="https://en.wikipedia.org/wiki/The_Bear", description=""),
            SearchResult(title="Netflix", url="https://www.netflix.com/title/99999", description=""),
        ]

        result = await search_content(title="The Bear", brave_client=mock_brave_client)

        assert result.success is True
        assert len(result.matches) == 1
        assert result.matches[0].content_id == "99999"

    @pytest.mark.asyncio
    async def test_to_tool_result_is_json(self, mock_brave_client: AsyncMock) -> None:
        import json

        mock_brave_client.search.return_value = [
            SearchResult(title="Show", url="https://www.netflix.com/title/12345", description=""),
        ]
        result = await search_content(title="Show", brave_client=mock_brave_client)
        tool_result = result.to_tool_result()
        parsed = json.loads(tool_result)

        assert parsed["success"] is True
        assert len(parsed["matches"]) == 1
        assert parsed["matches"][0]["service_name"] == "netflix"
        assert parsed["matches"][0]["channel_id"] == 12


class TestSearchContentFailures:
    @pytest.mark.asyncio
    async def test_no_search_results(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = []

        result = await search_content(title="Nonexistent Show", brave_client=mock_brave_client)

        assert result.success is False
        assert "No search results" in result.message
        assert result.matches == []

    @pytest.mark.asyncio
    async def test_no_matching_urls(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="Result", url="https://www.imdb.com/title/tt123/", description=""),
            SearchResult(title="Result", url="https://www.reddit.com/r/theshow", description=""),
        ]

        result = await search_content(title="Some Show", brave_client=mock_brave_client)

        assert result.success is False
        assert "no streaming service URLs matched" in result.message
        assert "imdb.com" in result.message
        assert result.matches == []

    @pytest.mark.asyncio
    async def test_brave_search_error(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.side_effect = BraveSearchError("API key invalid")

        result = await search_content(title="Show", brave_client=mock_brave_client)

        assert result.success is False
        assert "Search failed" in result.message


class TestSearchContentServiceSubset:
    @pytest.mark.asyncio
    async def test_limits_to_specified_services(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = []

        await search_content(title="Show", brave_client=mock_brave_client, services=[NETFLIX])

        call_args = mock_brave_client.search.call_args
        query = call_args.args[0]
        assert "site:netflix.com" in query
        assert "site:hulu.com" not in query


class TestLaunchOnRoku:
    @pytest.mark.asyncio
    async def test_successful_launch(self, mock_http_client: AsyncMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.post.return_value = mock_response

        result = await launch_on_roku(
            channel_id=12,
            content_id="81231974",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
        )

        assert result.success is True
        assert result.status_code == 200
        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        assert "/launch/12" in call_args.args[0]
        assert call_args.kwargs["params"]["contentId"] == "81231974"
        assert call_args.kwargs["params"]["mediaType"] == "movie"

    @pytest.mark.asyncio
    async def test_launch_failure_status(self, mock_http_client: AsyncMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_http_client.post.return_value = mock_response

        result = await launch_on_roku(
            channel_id=12,
            content_id="12345",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
        )

        assert result.success is False
        assert "status 500" in result.message

    @pytest.mark.asyncio
    async def test_launch_network_error(self, mock_http_client: AsyncMock) -> None:
        mock_http_client.post.side_effect = httpx.ConnectError("Connection refused")

        result = await launch_on_roku(
            channel_id=12,
            content_id="12345",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
        )

        assert result.success is False
        assert "Roku connection failed" in result.message

    @pytest.mark.asyncio
    async def test_media_type_in_url(self, mock_http_client: AsyncMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.post.return_value = mock_response

        await launch_on_roku(
            channel_id=2285,
            content_id="abc-123",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
            media_type="episode",
        )

        call_args = mock_http_client.post.call_args
        assert call_args.kwargs["params"]["mediaType"] == "episode"
