"""Tests for the content search and Roku launch pipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.brave_search import BraveSearchError, SearchResult
from app.core.emby import EmbyError, EmbyItem
from app.core.search_and_play import ContentMatch, build_search_query, launch_on_roku, search_content
from app.core.streaming import AMAZON_PRIME, HULU, NETFLIX
from app.core.tmdb import TitleAvailability

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


_PITT_SHOW_URL = "https://www.max.com/shows/the-pitt/e6e7bad9-d48d-4434-b334-7c651ffc4bdf"
_PITT_SHOW_ID = "e6e7bad9-d48d-4434-b334-7c651ffc4bdf"
_PITT_EP1_ID = "e4b915fb-5e6b-42b8-97ac-90ec7d0e3147"
# Show-page HTML embeds episode links as /shows/{slug}/s{N}/{show-uuid}/{ep-slug}/{ep-uuid}.
# The decoy is another show's episode link (recommendation tile): resolution
# must only accept episode ids whose path carries OUR show uuid.
_PITT_PAGE_HTML = (
    '<a href="/shows/white-lotus/s1/14f9834d-bc23-41a8-ab61-5c8abdbea505'
    '/e1-arrivals/deadbeef-0000-4000-8000-000000000000">decoy</a>'
    f'<a href="/shows/pitt-2024/s1/{_PITT_SHOW_ID}/e1-700-am/{_PITT_EP1_ID}">E1</a>'
)


def _page_response(html: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.text = html
    return response


class TestMaxShowResolution:
    """Max show-page matches resolve to a playable episode UUID before launch.

    Device-verified 2026-08-15: the Max Roku app rejects show-entity UUIDs
    ("This video is not available") but plays any of the show's episode UUIDs;
    with mediaType=series it smart-bookmarks to the next unwatched episode
    regardless of which episode uuid is passed.
    """

    @pytest.mark.asyncio
    async def test_show_page_resolves_to_episode_id(
        self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="The Pitt | Max", url=_PITT_SHOW_URL, description="")
        ]
        mock_http_client.get.return_value = _page_response(_PITT_PAGE_HTML)

        result = await search_content(
            title="The Pitt",
            brave_client=mock_brave_client,
            http_client=mock_http_client,
        )

        assert result.success is True
        assert len(result.matches) == 1
        match = result.matches[0]
        assert match.service_name == "max"
        assert match.content_id == _PITT_EP1_ID
        assert match.media_type == "series"
        # Resolution fetched the show page itself
        fetched_url = mock_http_client.get.call_args[0][0]
        assert fetched_url == _PITT_SHOW_URL

    @pytest.mark.asyncio
    async def test_unresolvable_show_page_does_not_block_service(
        self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock
    ) -> None:
        """Spec §11 semantics: a rejected candidate must not satisfy or block
        its service — a later playable URL can still claim it."""
        mock_brave_client.search.return_value = [
            SearchResult(title="The Pitt | Max", url=_PITT_SHOW_URL, description=""),
            SearchResult(
                title="Watch The Pitt",
                url="https://www.max.com/video/watch/bd43b2a4-1639-4197-96d4-2ec14eb45e9e",
                description="",
            ),
        ]
        mock_http_client.get.return_value = _page_response("<html>no episode links here</html>")

        result = await search_content(
            title="The Pitt",
            brave_client=mock_brave_client,
            http_client=mock_http_client,
        )

        assert result.success is True
        assert len(result.matches) == 1
        assert result.matches[0].content_id == "bd43b2a4-1639-4197-96d4-2ec14eb45e9e"

    @pytest.mark.asyncio
    async def test_fetch_error_drops_candidate(self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock) -> None:
        """A show uuid is a guaranteed-broken launch, so unlike the Prime probe
        this step fails CLOSED: no resolution, no match."""
        mock_brave_client.search.return_value = [
            SearchResult(title="The Pitt | Max", url=_PITT_SHOW_URL, description="")
        ]
        mock_http_client.get.side_effect = httpx.ConnectError("boom")

        result = await search_content(
            title="The Pitt",
            brave_client=mock_brave_client,
            http_client=mock_http_client,
        )

        assert result.success is False
        assert result.matches == []

    @pytest.mark.asyncio
    async def test_episode_url_needs_no_resolution(
        self, mock_brave_client: AsyncMock, mock_http_client: AsyncMock
    ) -> None:
        episode_url = f"https://www.max.com/shows/pitt-2024/s1/{_PITT_SHOW_ID}/e1-700-am/{_PITT_EP1_ID}"
        mock_brave_client.search.return_value = [SearchResult(title="E1", url=episode_url, description="")]

        result = await search_content(
            title="The Pitt",
            brave_client=mock_brave_client,
            http_client=mock_http_client,
        )

        assert result.success is True
        assert result.matches[0].content_id == _PITT_EP1_ID
        assert result.matches[0].media_type == "episode"
        mock_http_client.get.assert_not_called()


class TestDeepLinkFlag:
    """Matches carry deep_link so callers can warn when a launch only opens the app."""

    @pytest.mark.asyncio
    async def test_apple_tv_match_flagged_no_deep_link(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(
                title="Severance",
                url="https://tv.apple.com/us/show/severance/umc.cmc.1srk2goyh2q2zdxcx605w8vtx",
                description="",
            )
        ]

        result = await search_content(title="Severance", brave_client=mock_brave_client)

        assert result.matches[0].service_name == "apple_tv_plus"
        assert result.matches[0].deep_link is False

    @pytest.mark.asyncio
    async def test_netflix_match_flagged_deep_link(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [
            SearchResult(title="The Bear", url="https://www.netflix.com/title/81231974", description="")
        ]

        result = await search_content(title="The Bear", brave_client=mock_brave_client)

        assert result.matches[0].deep_link is True


class TestLaunchOnRokuNoDeepLink:
    """Channels whose Roku app ignores deep links (Apple TV) get an honest message."""

    @pytest.mark.asyncio
    async def test_message_says_manual_selection_needed(self, mock_http_client: AsyncMock) -> None:
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        result = await launch_on_roku(
            channel_id=551012,
            content_id="umc.cmc.1srk2goyh2q2zdxcx605w8vtx",
            roku_base_url=ROKU_BASE_URL,
            http_client=mock_http_client,
            post_launch_key=None,
            supports_deep_link=False,
        )

        assert result.success is True
        assert mock_http_client.post.call_count == 1  # launch only, no keypress
        assert "does not support deep link" in result.message
        assert "manually" in result.message


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
    async def test_message_dedupes_repeated_service_names(
        self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock
    ) -> None:
        """Two Emby matches must read as one "emby" entry in the summary
        message, not "3 service(s): emby, emby, hulu"."""
        mock_emby_client.search.return_value = [
            EmbyItem(item_id="m1", name="Heat", item_type="Movie"),
            EmbyItem(item_id="m2", name="Heat (1995)", item_type="Movie"),
        ]
        mock_brave_client.search.return_value = [
            SearchResult(
                title="Heat | Hulu",
                url="https://www.hulu.com/series/the-bear-565d8976-9e52-4f30-a6f5-a47e7fe1abd4",
                description="",
            )
        ]

        result = await search_content("Heat", mock_brave_client, emby_client=mock_emby_client, services=[HULU])

        assert result.success is True
        assert len(result.matches) == 3
        assert result.message == "Found content on 2 service(s): emby, hulu"

    @pytest.mark.asyncio
    async def test_nothing_found_anywhere(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = []
        mock_brave_client.search.return_value = []

        result = await search_content("Nonexistent", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is False
        assert result.matches == []


class TestSearchContentEnrichment:
    """Poster and season/episode metadata stamped onto matches."""

    BEAR_HULU = SearchResult(
        title="The Bear | Hulu",
        url="https://www.hulu.com/series/the-bear-565d8976-9e52-4f30-a6f5-a47e7fe1abd4",
        description="",
    )
    POSTER = "https://image.tmdb.org/t/p/w342/bear.jpg"

    @pytest.mark.asyncio
    async def test_season_episode_stamped_on_matches(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.BEAR_HULU]

        result = await search_content("The Bear", mock_brave_client, season=2, episode=5, services=[HULU])

        assert result.matches[0].season == 2
        assert result.matches[0].episode == 5

    @pytest.mark.asyncio
    async def test_poster_stamped_on_streaming_and_emby_matches(
        self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_emby_client.search.return_value = [EmbyItem(item_id="s1", name="The Bear", item_type="Series")]
        mock_brave_client.search.return_value = [self.BEAR_HULU]
        mock_tmdb_client.get_availability.return_value = TitleAvailability(streamable={"hulu"}, poster_url=self.POSTER)

        result = await search_content(
            "The Bear",
            mock_brave_client,
            emby_client=mock_emby_client,
            tmdb_client=mock_tmdb_client,
            services=[HULU],
        )

        assert [m.service_name for m in result.matches] == ["emby", "hulu"]
        assert [m.poster_url for m in result.matches] == [self.POSTER, self.POSTER]

    @pytest.mark.asyncio
    async def test_emby_only_result_still_gets_poster(
        self, mock_emby_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        """Even with no Brave client at all, the oracle runs for the poster —
        and an empty streamable set must never filter the Emby match."""
        mock_emby_client.search.return_value = [EmbyItem(item_id="m1", name="Heat", item_type="Movie")]
        mock_tmdb_client.get_availability.return_value = TitleAvailability(streamable=set(), poster_url=self.POSTER)

        result = await search_content("Heat", None, emby_client=mock_emby_client, tmdb_client=mock_tmdb_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["emby"]
        assert result.matches[0].poster_url == self.POSTER

    @pytest.mark.asyncio
    async def test_no_tmdb_client_leaves_enrichment_none(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.BEAR_HULU]

        result = await search_content("The Bear", mock_brave_client, services=[HULU])

        assert result.matches[0].poster_url is None
        assert result.matches[0].season is None
        assert result.matches[0].episode is None


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
        mock_tmdb_client.get_availability.return_value = TitleAvailability(streamable={"hulu"}, poster_url=None)

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
        mock_tmdb_client.get_availability.return_value = None

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
        mock_tmdb_client.get_availability.side_effect = RuntimeError("boom")

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

        async def _slow(title: str, tv_only: bool = False) -> TitleAvailability:
            await _asyncio.sleep(30)
            return TitleAvailability(streamable=set(), poster_url=None)

        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_availability.side_effect = _slow

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
        mock_tmdb_client.get_availability.return_value = TitleAvailability(streamable=set(), poster_url=None)

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
        mock_tmdb_client.get_availability.return_value = TitleAvailability(streamable={"hulu"}, poster_url=None)

        await search_content(
            title="The Bear",
            season=3,
            brave_client=mock_brave_client,
            services=[HULU],
            tmdb_client=mock_tmdb_client,
        )

        mock_tmdb_client.get_availability.assert_awaited_once_with("The Bear", tv_only=True)

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
    async def test_no_matches_at_all_reports_search_failure(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        """With nothing found anywhere the failure message reflects the search,
        not the oracle; the in-flight lookup is dropped without waiting."""
        import asyncio as _asyncio

        async def _never(title: str, tv_only: bool = False) -> TitleAvailability:
            await _asyncio.sleep(30)
            raise AssertionError("unreachable")

        mock_brave_client.search.return_value = []
        mock_tmdb_client.get_availability.side_effect = _never

        result = await search_content(
            "Nonexistent",
            mock_brave_client,
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is False
        assert result.matches == []
        assert "No search results found" in result.message

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
        mock_tmdb_client.get_availability.return_value = TitleAvailability(streamable={"hulu"}, poster_url=None)

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
