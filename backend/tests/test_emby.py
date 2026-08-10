"""Tests for the Emby media server client."""

import json
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


class TestSearchTitleFilter:
    """Emby's SearchTerm is fuzzy (matches on any shared word); results whose
    title doesn't actually correspond to the query must be dropped."""

    @pytest.mark.asyncio
    async def test_fuzzy_noise_dropped(self, client: EmbyClient, http_client: AsyncMock) -> None:
        """'Charlie bit my finger' must not surface 'My Neighbor Totoro' just
        because both contain 'my' (observed live)."""
        http_client.get.return_value = _response(
            {"Items": [_movie("m1", "My Neighbor Totoro"), _movie("m2", "My Life as a Dog")]}
        )

        items = await client.search("Charlie bit my finger")

        assert items == []

    @pytest.mark.asyncio
    async def test_exact_title_kept(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie("m1", "Heat")]})

        items = await client.search("Heat")

        assert [i.name for i in items] == ["Heat"]

    @pytest.mark.asyncio
    async def test_decorated_library_name_kept(self, client: EmbyClient, http_client: AsyncMock) -> None:
        """Library naming quirks like a year suffix must not lose the match."""
        http_client.get.return_value = _response({"Items": [_movie("m1", "Heat (1995)")]})

        items = await client.search("Heat")

        assert [i.name for i in items] == ["Heat (1995)"]

    @pytest.mark.asyncio
    async def test_word_fragment_not_matched(self, client: EmbyClient, http_client: AsyncMock) -> None:
        """Containment is per word sequence, not per character: 'It' is not a
        match for 'The Italian Job'."""
        http_client.get.return_value = _response({"Items": [_movie("m1", "The Italian Job")]})

        items = await client.search("It")

        assert items == []

    @pytest.mark.asyncio
    async def test_filter_applies_before_episode_resolution(self, client: EmbyClient, http_client: AsyncMock) -> None:
        """A non-matching series is dropped without fetching its episodes."""
        http_client.get.side_effect = [
            _response({"Items": [_series("s1", "Severance"), _series("s2", "Everest")]}),
            _response({"Items": [{"Id": "ep25", "IndexNumber": 5, "Type": "Episode", "Name": "Trojan's Horse"}]}),
        ]

        items = await client.search("Severance", season=2, episode=5)

        assert [i.item_id for i in items] == ["ep25"]
        # Only Severance's episodes were fetched: initial search + one episode call
        assert http_client.get.call_count == 2


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

    @pytest.mark.asyncio
    async def test_non_json_response_raises_emby_error(self, client: EmbyClient, http_client: AsyncMock) -> None:
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
        http_client.get.return_value = resp

        with pytest.raises(EmbyError):
            await client.search("Heat")

    @pytest.mark.asyncio
    async def test_userdata_null_means_no_resume(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response(
            {"Items": [{"Id": "m1", "Name": "Heat", "Type": "Movie", "UserData": None}]}
        )

        items = await client.search("Heat")

        assert len(items) == 1
        assert items[0].resume_position_ticks is None
