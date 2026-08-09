"""Tests for the content search and Roku launch pipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.brave_search import BraveSearchError, SearchResult
from app.core.emby import EmbyError, EmbyItem
from app.core.search_and_play import ContentMatch, build_search_query, launch_on_roku, search_content
from app.core.streaming import AMAZON_PRIME, HULU, NETFLIX

ROKU_BASE_URL = "http://192.168.1.100:8060"

# Bye Bye Birdie, the case that motivated spec §4/§11 verification: the DVD's
# retail ASIN and the Prime Video title share the /dp/{ASIN} URL shape.
DVD_URL = "https://www.amazon.com/Bye-Birdie-Jason-Alexander/dp/B0002V7TDI"
VIDEO_URL = "https://www.amazon.com/Bye-Birdie-Janet-Leigh/dp/B001G5RFHE"


def _probe_response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


class TestBuildSearchQuery:
    """Queries lead with "watch" per roku-deeplink-spec §11: bare-title queries
    rank retail/placeholder pages above streaming pages."""

    def test_title_only(self) -> None:
        assert build_search_query("The Bear") == "watch The Bear"

    def test_title_and_season(self) -> None:
        assert build_search_query("The Bear", season=3) == "watch The Bear Season 3"

    def test_full_episode(self) -> None:
        result = build_search_query("Rick and Morty", season=4, episode=5, episode_title="Rattlestar Ricklactica")
        assert result == "watch Rick and Morty Season 4 Episode 5 Rattlestar Ricklactica"

    def test_title_and_episode_title(self) -> None:
        result = build_search_query("Rick and Morty", episode_title="Rattlestar Ricklactica")
        assert result == "watch Rick and Morty Rattlestar Ricklactica"


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


class TestSearchContentVerification:
    """Prime Video ASIN verification (spec §4) and candidate handling (§11)."""

    @pytest.mark.asyncio
    async def test_rejected_candidate_does_not_lock_service(
        self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock
    ) -> None:
        """A probe-rejected DVD ASIN must leave the service claimable by the
        real Prime Video ASIN ranked below it."""
        mock_brave_client.search.return_value = [
            SearchResult(title="Amazon.com: Bye Bye Birdie : DVD", url=DVD_URL, description=""),
            SearchResult(title="Watch Bye Bye Birdie | Prime Video", url=VIDEO_URL, description=""),
        ]
        mock_http_client.get.side_effect = [_probe_response(404), _probe_response(200)]

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[AMAZON_PRIME],
            http_client=mock_http_client,
        )

        assert result.success is True
        assert len(result.matches) == 1
        assert result.matches[0].content_id == "B001G5RFHE"
        probe_urls = [c.args[0] for c in mock_http_client.get.call_args_list]
        assert probe_urls == [
            "https://www.primevideo.com/detail/B0002V7TDI",
            "https://www.primevideo.com/detail/B001G5RFHE",
        ]

    @pytest.mark.asyncio
    async def test_all_candidates_rejected(self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="Amazon.com: Bye Bye Birdie : DVD", url=DVD_URL, description=""),
        ]
        mock_http_client.get.return_value = _probe_response(404)

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[AMAZON_PRIME],
            http_client=mock_http_client,
        )

        assert result.success is False
        assert result.matches == []

    @pytest.mark.asyncio
    async def test_unambiguous_video_paths_skip_probe(
        self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock
    ) -> None:
        """/gp/video/ paths are inherently video pages — no probe needed."""
        mock_brave_client.search.return_value = [
            SearchResult(
                title="Watch Bye Bye Birdie | Prime Video",
                url="https://www.amazon.com/gp/video/detail/B001G5RFHE",
                description="",
            ),
        ]

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[AMAZON_PRIME],
            http_client=mock_http_client,
        )

        assert result.success is True
        assert result.matches[0].content_id == "B001G5RFHE"
        mock_http_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_probe_error_fails_open(self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock) -> None:
        """A transient probe failure must never block a legitimate launch."""
        mock_brave_client.search.return_value = [
            SearchResult(title="Watch Bye Bye Birdie | Prime Video", url=VIDEO_URL, description=""),
        ]
        mock_http_client.get.side_effect = httpx.ConnectError("boom")

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[AMAZON_PRIME],
            http_client=mock_http_client,
        )

        assert result.success is True
        assert result.matches[0].content_id == "B001G5RFHE"

    @pytest.mark.asyncio
    async def test_bot_wall_status_fails_open(self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="Watch Bye Bye Birdie | Prime Video", url=VIDEO_URL, description=""),
        ]
        mock_http_client.get.return_value = _probe_response(503)

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[AMAZON_PRIME],
            http_client=mock_http_client,
        )

        assert result.success is True
        assert result.matches[0].content_id == "B001G5RFHE"

    @pytest.mark.asyncio
    async def test_no_http_client_skips_probe(self, mock_brave_client: AsyncMock) -> None:
        """Without a probe client, matches are accepted unverified (fail open)."""
        mock_brave_client.search.return_value = [
            SearchResult(title="Amazon.com: Bye Bye Birdie : DVD", url=DVD_URL, description=""),
        ]

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[AMAZON_PRIME],
        )

        assert result.success is True
        assert result.matches[0].content_id == "B0002V7TDI"


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
    """Tests for launch_on_roku action sequence (launch → wait → keypress)."""

    @pytest.mark.asyncio
    async def test_successful_launch_action_sequence(self, mock_http_client: AsyncMock) -> None:
        """Test full action sequence: launch, wait, keypress."""
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
        # Should call POST twice: launch + keypress
        assert mock_http_client.post.call_count == 2

        calls = mock_http_client.post.call_args_list
        # First call: launch
        assert "/launch/12" in calls[0].args[0]
        assert calls[0].kwargs["params"]["contentId"] == "81231974"
        assert calls[0].kwargs["params"]["mediaType"] == "movie"
        # Second call: keypress (default is Select)
        assert "/keypress/Select" in calls[1].args[0]

    @pytest.mark.asyncio
    async def test_netflix_uses_play_key(self, mock_http_client: AsyncMock) -> None:
        """Test that Netflix uses Play key instead of Select."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.post.return_value = mock_response

        result = await launch_on_roku(
            channel_id=12,
            content_id="81231974",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
            post_launch_key="Play",
        )

        assert result.success is True
        calls = mock_http_client.post.call_args_list
        assert "/keypress/Play" in calls[1].args[0]

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
        # Should only call launch once (fails before keypress)
        assert mock_http_client.post.call_count == 1

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
    async def test_media_type_in_params(self, mock_http_client: AsyncMock) -> None:
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

        calls = mock_http_client.post.call_args_list
        assert calls[0].kwargs["params"]["mediaType"] == "episode"

    @pytest.mark.asyncio
    async def test_keypress_failure_after_successful_launch(self, mock_http_client: AsyncMock) -> None:
        """Test handling when launch succeeds but keypress fails."""
        launch_response = MagicMock()
        launch_response.status_code = 200
        keypress_response = MagicMock()
        keypress_response.status_code = 500
        mock_http_client.post.side_effect = [launch_response, keypress_response]

        result = await launch_on_roku(
            channel_id=12,
            content_id="12345",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
        )

        assert result.success is False
        assert "keypress returned status 500" in result.message


class TestLaunchOnRokuEmby:
    """Emby (channel 44191) is launch-only per roku-deeplink spec."""

    @pytest.mark.asyncio
    async def test_emby_launch_only(self, mock_http_client: AsyncMock) -> None:
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await launch_on_roku(
                channel_id=44191,
                content_id="3f9a1c",
                roku_base_url="http://192.168.1.100:8060",
                http_client=mock_http_client,
            )

        assert result.success is True
        assert mock_http_client.post.call_count == 1
        args, kwargs = mock_http_client.post.call_args
        assert args[0] == "http://192.168.1.100:8060/launch/44191"
        assert kwargs["params"] == {"Command": "PlayNow", "ItemIds": "3f9a1c"}
        # dict == is order-insensitive; assert insertion order explicitly since
        # it determines serialized query-string order (spec-fixture order).
        assert list(kwargs["params"].keys()) == ["Command", "ItemIds"]
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emby_resume_position(self, mock_http_client: AsyncMock) -> None:
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        await launch_on_roku(
            channel_id=44191,
            content_id="3f9a1c",
            roku_base_url="http://192.168.1.100:8060",
            http_client=mock_http_client,
            resume_position_ticks=12000000000,
        )

        _, kwargs = mock_http_client.post.call_args
        assert kwargs["params"] == {
            "Command": "PlayNow",
            "ItemIds": "3f9a1c",
            "StartPositionTicks": "12000000000",
        }
        # dict == is order-insensitive; assert insertion order explicitly since
        # it determines serialized query-string order (spec-fixture order).
        assert list(kwargs["params"].keys()) == ["Command", "ItemIds", "StartPositionTicks"]

    @pytest.mark.asyncio
    async def test_emby_ignores_post_launch_key(self, mock_http_client: AsyncMock) -> None:
        """Even if a caller passes a key, Emby never gets a keypress."""
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        await launch_on_roku(
            channel_id=44191,
            content_id="3f9a1c",
            roku_base_url="http://192.168.1.100:8060",
            http_client=mock_http_client,
            post_launch_key="Select",
        )

        assert mock_http_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_none_key_is_launch_only_for_any_channel(self, mock_http_client: AsyncMock) -> None:
        """Spec Function 2: a descriptor with no post_launch_key is launch-only."""
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await launch_on_roku(
                channel_id=12,
                content_id="81444554",
                roku_base_url="http://192.168.1.100:8060",
                http_client=mock_http_client,
                post_launch_key=None,
            )

        assert result.success is True
        assert mock_http_client.post.call_count == 1
        _, kwargs = mock_http_client.post.call_args
        assert kwargs["params"] == {"contentId": "81444554", "mediaType": "movie"}
        mock_sleep.assert_not_awaited()


class TestContentMatchFields:
    def test_launch_only_match(self) -> None:
        match = ContentMatch(
            service_name="emby",
            channel_id=44191,
            content_id="3f9a1c",
            source_url="http://emby.local:8096/web/index.html#!/item?id=3f9a1c",
            title="Heat",
            media_type="movie",
            post_launch_key=None,
            resume_position_ticks=12000000000,
        )
        assert match.post_launch_key is None
        assert match.resume_position_ticks == 12000000000

    def test_resume_defaults_to_none(self) -> None:
        match = ContentMatch(
            service_name="netflix",
            channel_id=12,
            content_id="81444554",
            source_url="https://netflix.com/watch/81444554",
            title="Heat",
            media_type="movie",
        )
        assert match.post_launch_key == "Select"
        assert match.resume_position_ticks is None


class TestSearchContentEmby:
    @pytest.mark.asyncio
    async def test_emby_matches_come_first(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = [
            EmbyItem(item_id="m1", name="Heat", item_type="Movie", resume_position_ticks=42)
        ]
        mock_brave_client.search.return_value = [
            SearchResult(title="Heat | Netflix", url="https://www.netflix.com/watch/81444554", description="")
        ]

        result = await search_content("Heat", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["emby", "netflix"]
        emby = result.matches[0]
        assert emby.channel_id == 44191
        assert emby.content_id == "m1"
        assert emby.title == "Heat"
        assert emby.media_type == "movie"
        assert emby.post_launch_key is None
        assert emby.resume_position_ticks == 42
        assert emby.source_url == "http://emby.local:8096/web/index.html#!/item?id=m1"

    @pytest.mark.asyncio
    async def test_series_media_type(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = [EmbyItem(item_id="s1", name="Severance", item_type="Series")]
        mock_brave_client.search.return_value = []

        result = await search_content("Severance", mock_brave_client, emby_client=mock_emby_client)

        assert result.matches[0].media_type == "series"

    @pytest.mark.asyncio
    async def test_emby_failure_degrades_to_streaming(
        self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock
    ) -> None:
        mock_emby_client.search.side_effect = EmbyError("server down")
        mock_brave_client.search.return_value = [
            SearchResult(title="Heat | Netflix", url="https://www.netflix.com/watch/81444554", description="")
        ]

        result = await search_content("Heat", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["netflix"]

    @pytest.mark.asyncio
    async def test_emby_passes_season_episode(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = []
        mock_brave_client.search.return_value = []

        await search_content("Severance", mock_brave_client, season=2, episode=3, emby_client=mock_emby_client)

        mock_emby_client.search.assert_awaited_once_with("Severance", season=2, episode=3)

    @pytest.mark.asyncio
    async def test_emby_only_no_brave(self, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = [EmbyItem(item_id="m1", name="Heat", item_type="Movie")]

        result = await search_content("Heat", None, emby_client=mock_emby_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["emby"]

    @pytest.mark.asyncio
    async def test_nothing_found_anywhere(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = []
        mock_brave_client.search.return_value = []

        result = await search_content("Nonexistent", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is False
        assert result.matches == []


class TestSearchContentAvailabilityFilter:
    """TMDB oracle filtering: drop only on affirmative absence, fail open otherwise."""

    NETFLIX_RESULT = SearchResult(
        title="Watch Bye Bye Birdie | Netflix",
        url="https://www.netflix.com/title/342088",
        description="",
    )
    HULU_RESULT = SearchResult(
        title="The Bear | Hulu",
        url="https://www.hulu.com/series/the-bear-565d8976-9e52-4f30-a6f5-a47e7fe1abd4",
        description="",
    )

    @pytest.mark.asyncio
    async def test_drops_service_absent_from_oracle(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT, self.HULU_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = {"hulu"}

        result = await search_content(
            title="The Bear",
            brave_client=mock_brave_client,
            services=[NETFLIX, HULU],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["hulu"]
        assert "filtered netflix (not streamable per TMDB)" in result.message

    @pytest.mark.asyncio
    async def test_oracle_none_keeps_everything(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT, self.HULU_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = None

        result = await search_content(
            title="The Bear",
            brave_client=mock_brave_client,
            services=[NETFLIX, HULU],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert len(result.matches) == 2
        assert "filtered" not in result.message

    @pytest.mark.asyncio
    async def test_oracle_exception_fails_open(self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_streamable_services.side_effect = RuntimeError("boom")

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[NETFLIX],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert len(result.matches) == 1

    @pytest.mark.asyncio
    async def test_oracle_timeout_fails_open(self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock) -> None:
        import asyncio as _asyncio

        async def _slow(title: str, tv_only: bool = False) -> set[str]:
            await _asyncio.sleep(30)
            return set()

        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_streamable_services.side_effect = _slow

        with patch("app.core.search_and_play.ORACLE_TIMEOUT_SECONDS", 0.01):
            result = await search_content(
                title="Bye Bye Birdie",
                brave_client=mock_brave_client,
                services=[NETFLIX],
                tmdb_client=mock_tmdb_client,
            )

        assert result.success is True
        assert len(result.matches) == 1

    @pytest.mark.asyncio
    async def test_all_matches_filtered_fails_with_explanation(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = set()

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[NETFLIX],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is False
        assert result.matches == []
        assert "filtered netflix (not streamable per TMDB)" in result.message

    @pytest.mark.asyncio
    async def test_tv_hint_from_season(self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.HULU_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = {"hulu"}

        await search_content(
            title="The Bear",
            season=3,
            brave_client=mock_brave_client,
            services=[HULU],
            tmdb_client=mock_tmdb_client,
        )

        mock_tmdb_client.get_streamable_services.assert_awaited_once_with("The Bear", tv_only=True)

    @pytest.mark.asyncio
    async def test_no_tmdb_client_unchanged(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[NETFLIX],
        )

        assert result.success is True
        assert len(result.matches) == 1

    @pytest.mark.asyncio
    async def test_emby_match_exempt_from_filtering(
        self,
        mock_brave_client: AsyncMock,
        mock_emby_client: AsyncMock,
        mock_tmdb_client: AsyncMock,
    ) -> None:
        """Emby is the user's own library; the oracle never says "emby", so an
        unexempted filter would wrongly drop every Emby match."""
        mock_emby_client.search.return_value = [EmbyItem(item_id="m1", name="Heat", item_type="Movie")]
        mock_brave_client.search.return_value = [
            SearchResult(title="Heat | Netflix", url="https://www.netflix.com/watch/81444554", description="")
        ]
        mock_tmdb_client.get_streamable_services.return_value = {"hulu"}

        result = await search_content(
            title="Heat",
            brave_client=mock_brave_client,
            services=[NETFLIX, HULU],
            emby_client=mock_emby_client,
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["emby"]
        assert "filtered netflix (not streamable per TMDB)" in result.message
