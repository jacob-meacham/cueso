"""Tests for the TMDB watch-providers availability oracle."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.tmdb import PROVIDER_ID_TO_SERVICE, TMDBClient, normalize_title


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _multi_result(media_type: str, tmdb_id: int, title: str) -> dict[str, Any]:
    key = "title" if media_type == "movie" else "name"
    return {"media_type": media_type, "id": tmdb_id, key: title}


def _providers_payload(region: str, bucket_to_ids: dict[str, list[int]]) -> dict[str, Any]:
    block = {
        bucket: [{"provider_id": pid, "provider_name": str(pid)} for pid in ids]
        for bucket, ids in bucket_to_ids.items()
    }
    return {"results": {region: block}}


def _client_with(routes: dict[str, MagicMock]) -> tuple[TMDBClient, AsyncMock]:
    """TMDBClient over a mock http client routing GETs by URL substring."""
    http_client = AsyncMock(spec=httpx.AsyncClient)

    async def _get(url: str, **kwargs: Any) -> MagicMock:
        for fragment, response in routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected TMDB GET: {url}")

    http_client.get.side_effect = _get
    return TMDBClient(api_key="k", http_client=http_client), http_client


class TestNormalizeTitle:
    def test_casefold_punctuation_whitespace(self) -> None:
        assert normalize_title("Bye-Bye  Birdie!") == "bye bye birdie"
        assert normalize_title("The Bear") == "the bear"

    def test_distinct_titles_stay_distinct(self) -> None:
        assert normalize_title("Masha and the Bear") != normalize_title("The Bear")


class TestProviderMap:
    def test_exact_provider_id_mapping(self) -> None:
        assert PROVIDER_ID_TO_SERVICE == {
            8: "netflix",
            1796: "netflix",
            9: "amazon_prime",
            10: "amazon_prime",
            2100: "amazon_prime",
            337: "disney_plus",
            15: "hulu",
            1899: "max",
            384: "max",
            350: "apple_tv_plus",
            2: "apple_tv_plus",
        }


class TestGetStreamableServices:
    @pytest.mark.asyncio
    async def test_union_across_adaptations(self) -> None:
        """1963 (rent/buy on Amazon Video id 10) + 1995 (Prime id 9): both map
        to amazon_prime; netflix is absent -> not in the set."""
        client, _ = _client_with(
            {
                "/search/multi": _response(
                    {
                        "results": [
                            _multi_result("movie", 1, "Bye Bye Birdie"),
                            _multi_result("movie", 2, "Bye Bye Birdie"),
                        ]
                    }
                ),
                "/movie/1/watch/providers": _response(_providers_payload("US", {"buy": [10], "rent": [2]})),
                "/movie/2/watch/providers": _response(_providers_payload("US", {"flatrate": [9]})),
            }
        )
        services = await client.get_streamable_services("Bye Bye Birdie")
        assert services == {"amazon_prime", "apple_tv_plus"}

    @pytest.mark.asyncio
    async def test_title_collision_excluded_by_normalization(self) -> None:
        """ "Masha and the Bear" (netflix) must not rescue netflix for "The Bear"."""
        client, _ = _client_with(
            {
                "/search/multi": _response(
                    {
                        "results": [
                            _multi_result("tv", 1, "The Bear"),
                            _multi_result("tv", 2, "Masha and the Bear"),
                        ]
                    }
                ),
                "/tv/1/watch/providers": _response(_providers_payload("US", {"flatrate": [15, 337]})),
                "/tv/2/watch/providers": _response(_providers_payload("US", {"flatrate": [8]})),
            }
        )
        services = await client.get_streamable_services("The Bear")
        assert services == {"hulu", "disney_plus"}

    @pytest.mark.asyncio
    async def test_tv_only_excludes_movies(self) -> None:
        client, http_client = _client_with(
            {
                "/search/multi": _response(
                    {
                        "results": [
                            _multi_result("movie", 1, "The Bear"),
                            _multi_result("tv", 2, "The Bear"),
                        ]
                    }
                ),
                "/tv/2/watch/providers": _response(_providers_payload("US", {"flatrate": [15]})),
            }
        )
        services = await client.get_streamable_services("The Bear", tv_only=True)
        assert services == {"hulu"}
        urls = [c.args[0] for c in http_client.get.call_args_list]
        assert not any("/movie/" in u for u in urls)

    @pytest.mark.asyncio
    async def test_no_candidates_returns_none(self) -> None:
        client, _ = _client_with({"/search/multi": _response({"results": []})})
        assert await client.get_streamable_services("Nonexistent") is None

    @pytest.mark.asyncio
    async def test_no_plausible_candidates_returns_none(self) -> None:
        client, _ = _client_with(
            {"/search/multi": _response({"results": [_multi_result("movie", 1, "Something Else")]})}
        )
        assert await client.get_streamable_services("The Bear") is None

    @pytest.mark.asyncio
    async def test_region_absent_for_all_candidates_returns_none(self) -> None:
        client, _ = _client_with(
            {
                "/search/multi": _response({"results": [_multi_result("movie", 1, "The Bear")]}),
                "/movie/1/watch/providers": _response({"results": {}}),
            }
        )
        assert await client.get_streamable_services("The Bear") is None

    @pytest.mark.asyncio
    async def test_region_present_but_unmapped_is_empty_set(self) -> None:
        """Region data exists but only unmapped providers -> affirmative empty set."""
        client, _ = _client_with(
            {
                "/search/multi": _response({"results": [_multi_result("movie", 1, "The Bear")]}),
                "/movie/1/watch/providers": _response(_providers_payload("US", {"flatrate": [99999]})),
            }
        )
        assert await client.get_streamable_services("The Bear") == set()

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self) -> None:
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.get.side_effect = httpx.ConnectError("boom")
        client = TMDBClient(api_key="k", http_client=http_client)
        assert await client.get_streamable_services("The Bear") is None

    @pytest.mark.asyncio
    async def test_candidate_cap_is_three(self) -> None:
        client, http_client = _client_with(
            {
                "/search/multi": _response({"results": [_multi_result("movie", i, "The Bear") for i in range(1, 6)]}),
                "/watch/providers": _response(_providers_payload("US", {"flatrate": [8]})),
            }
        )
        await client.get_streamable_services("The Bear")
        provider_calls = [c for c in http_client.get.call_args_list if "/watch/providers" in c.args[0]]
        assert len(provider_calls) == 3

    @pytest.mark.asyncio
    async def test_custom_region(self) -> None:
        http_client = AsyncMock(spec=httpx.AsyncClient)

        async def _get(url: str, **kwargs: Any) -> MagicMock:
            if "/search/multi" in url:
                return _response({"results": [_multi_result("movie", 1, "The Bear")]})
            return _response(_providers_payload("GB", {"flatrate": [8]}))

        http_client.get.side_effect = _get
        client = TMDBClient(api_key="k", region="GB", http_client=http_client)
        assert await client.get_streamable_services("The Bear") == {"netflix"}
