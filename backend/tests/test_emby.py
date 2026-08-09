"""Tests for the Emby media server client."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.emby import EMBY_CHANNEL_ID, EmbyClient, EmbyError


def _response(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _movie(item_id: str = "m1", name: str = "Heat", ticks: int = 0) -> dict[str, Any]:
    return {"Id": item_id, "Name": name, "Type": "Movie", "UserData": {"PlaybackPositionTicks": ticks}}


def _series(item_id: str = "s1", name: str = "Severance") -> dict[str, Any]:
    return {"Id": item_id, "Name": name, "Type": "Series", "UserData": {"PlaybackPositionTicks": 0}}


@pytest.fixture
def http_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def client(http_client: AsyncMock) -> EmbyClient:
    return EmbyClient("http://emby.local:8096/", "key123", "user1", http_client=http_client)


def test_channel_id_matches_spec() -> None:
    assert EMBY_CHANNEL_ID == 44191


def test_server_url_trailing_slash_stripped(client: EmbyClient) -> None:
    assert client.server_url == "http://emby.local:8096"


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_request_shape(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": []})

        await client.search("Heat")

        args, kwargs = http_client.get.call_args
        assert args[0] == "http://emby.local:8096/emby/Users/user1/Items"
        assert kwargs["headers"] == {"X-Emby-Token": "key123"}
        assert kwargs["params"]["SearchTerm"] == "Heat"
        assert kwargs["params"]["IncludeItemTypes"] == "Movie,Series"
        assert kwargs["params"]["Recursive"] == "true"

    @pytest.mark.asyncio
    async def test_parses_items(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie(ticks=12000000000)]})

        items = await client.search("Heat")

        assert len(items) == 1
        assert items[0].item_id == "m1"
        assert items[0].name == "Heat"
        assert items[0].item_type == "Movie"
        assert items[0].resume_position_ticks == 12000000000

    @pytest.mark.asyncio
    async def test_zero_ticks_means_no_resume(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie(ticks=0)]})

        items = await client.search("Heat")

        assert items[0].resume_position_ticks is None

    @pytest.mark.asyncio
    async def test_season_episode_resolves_episode(self, client: EmbyClient, http_client: AsyncMock) -> None:
        episode = {
            "Id": "ep23",
            "Name": "The We We Are",
            "Type": "Episode",
            "IndexNumber": 3,
            "UserData": {"PlaybackPositionTicks": 500},
        }
        http_client.get.side_effect = [
            _response({"Items": [_series()]}),
            _response({"Items": [{"Id": "ep21", "IndexNumber": 1, "Type": "Episode", "Name": "e1"}, episode]}),
        ]

        items = await client.search("Severance", season=2, episode=3)

        assert len(items) == 1
        assert items[0].item_id == "ep23"
        assert items[0].item_type == "Episode"
        assert items[0].resume_position_ticks == 500
        args, kwargs = http_client.get.call_args
        assert args[0] == "http://emby.local:8096/emby/Shows/s1/Episodes"
        assert kwargs["params"]["Season"] == 2
        assert kwargs["params"]["UserId"] == "user1"

    @pytest.mark.asyncio
    async def test_episode_not_found_drops_series(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.side_effect = [
            _response({"Items": [_series()]}),
            _response({"Items": [{"Id": "ep21", "IndexNumber": 1, "Type": "Episode", "Name": "e1"}]}),
        ]

        items = await client.search("Severance", season=2, episode=99)

        assert items == []

    @pytest.mark.asyncio
    async def test_movie_kept_when_season_episode_given(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie()]})

        items = await client.search("Heat", season=2, episode=3)

        assert len(items) == 1
        assert items[0].item_type == "Movie"
        assert http_client.get.call_count == 1  # no episode lookup for movies

    @pytest.mark.asyncio
    async def test_http_status_error_raises_emby_error(self, client: EmbyClient, http_client: AsyncMock) -> None:
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401, text="Unauthorized")
        )
        http_client.get.return_value = resp

        with pytest.raises(EmbyError):
            await client.search("Heat")

    @pytest.mark.asyncio
    async def test_request_error_raises_emby_error(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(EmbyError):
            await client.search("Heat")
