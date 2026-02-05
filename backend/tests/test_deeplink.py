"""Tests for the deeplink module per roku-deeplink-spec.

Test fixtures from https://github.com/jacob-meacham/roku-deeplink-spec/blob/main/test_fixtures.json
"""

import pytest

from app.core.deeplink import (
    ExtractionResult,
    KeypressAction,
    LaunchAction,
    WaitAction,
    build_playback_command,
    convert_url_to_ecp_command,
)


class TestConvertUrlToEcpCommandValidUrls:
    """Test valid URLs that should match and extract content info."""

    def test_netflix_watch_url(self) -> None:
        result = convert_url_to_ecp_command("https://www.netflix.com/watch/81444554")
        assert result is not None
        assert result.channel_id == "12"
        assert result.channel_name == "Netflix"
        assert result.content_id == "81444554"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Play"

    def test_netflix_watch_without_www(self) -> None:
        result = convert_url_to_ecp_command("https://netflix.com/watch/12345")
        assert result is not None
        assert result.channel_id == "12"
        assert result.channel_name == "Netflix"
        assert result.content_id == "12345"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Play"

    def test_netflix_title_url_series(self) -> None:
        result = convert_url_to_ecp_command("https://www.netflix.com/title/80179766")
        assert result is not None
        assert result.channel_id == "12"
        assert result.channel_name == "Netflix"
        assert result.content_id == "80179766"
        assert result.media_type == "series"
        assert result.post_launch_key == "Play"

    def test_disney_plus_play_url(self) -> None:
        result = convert_url_to_ecp_command(
            "https://www.disneyplus.com/play/f63db666-b097-4c61-99c1-b778de2d4ae1"
        )
        assert result is not None
        assert result.channel_id == "291097"
        assert result.channel_name == "Disney+"
        assert result.content_id == "f63db666-b097-4c61-99c1-b778de2d4ae1"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_disney_plus_video_url(self) -> None:
        result = convert_url_to_ecp_command("https://disneyplus.com/video/abc-123-def")
        assert result is not None
        assert result.channel_id == "291097"
        assert result.channel_name == "Disney+"
        assert result.content_id == "abc-123-def"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_disney_plus_browse_entity_url(self) -> None:
        result = convert_url_to_ecp_command(
            "https://www.disneyplus.com/browse/entity-f63db666-b097-4c61-99c1-b778de2d4ae1"
        )
        assert result is not None
        assert result.channel_id == "291097"
        assert result.channel_name == "Disney+"
        assert result.content_id == "f63db666-b097-4c61-99c1-b778de2d4ae1"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_max_video_watch_url(self) -> None:
        result = convert_url_to_ecp_command(
            "https://www.max.com/video/watch/bd43b2a4-1639-4197-96d4-2ec14eb45e9e"
        )
        assert result is not None
        assert result.channel_id == "61322"
        assert result.channel_name == "HBO Max"
        assert result.content_id == "bd43b2a4-1639-4197-96d4-2ec14eb45e9e"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_max_play_url(self) -> None:
        result = convert_url_to_ecp_command("https://max.com/play/some-show-id")
        assert result is not None
        assert result.channel_id == "61322"
        assert result.channel_name == "HBO Max"
        assert result.content_id == "some-show-id"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_hbomax_legacy_domain(self) -> None:
        result = convert_url_to_ecp_command("https://www.hbomax.com/video/watch/legacy-id")
        assert result is not None
        assert result.channel_id == "61322"
        assert result.channel_name == "HBO Max"
        assert result.content_id == "legacy-id"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_max_movies_path(self) -> None:
        result = convert_url_to_ecp_command(
            "https://max.com/movies/dune-part-two/9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8"
        )
        assert result is not None
        assert result.channel_id == "61322"
        assert result.channel_name == "HBO Max"
        assert result.content_id == "9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_max_series_path(self) -> None:
        result = convert_url_to_ecp_command(
            "https://max.com/series/the-last-of-us/abc123-def456"
        )
        assert result is not None
        assert result.channel_id == "61322"
        assert result.channel_name == "HBO Max"
        assert result.content_id == "abc123-def456"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_amazon_gp_video_detail(self) -> None:
        result = convert_url_to_ecp_command(
            "https://www.amazon.com/gp/video/detail/B0DKTFF815"
        )
        assert result is not None
        assert result.channel_id == "13"
        assert result.channel_name == "Prime Video"
        assert result.content_id == "B0DKTFF815"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_amazon_gp_video_with_ref(self) -> None:
        result = convert_url_to_ecp_command(
            "https://amazon.com/gp/video/detail/B0FQM41JFJ/ref=xyz"
        )
        assert result is not None
        assert result.channel_id == "13"
        assert result.channel_name == "Prime Video"
        assert result.content_id == "B0FQM41JFJ"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_amazon_dp_url(self) -> None:
        result = convert_url_to_ecp_command("https://amazon.com/dp/B0FQM41JFJ/ref=xyz")
        assert result is not None
        assert result.channel_id == "13"
        assert result.channel_name == "Prime Video"
        assert result.content_id == "B0FQM41JFJ"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"

    def test_primevideo_detail(self) -> None:
        result = convert_url_to_ecp_command(
            "https://www.primevideo.com/detail/B0EXAMPL12"
        )
        assert result is not None
        assert result.channel_id == "13"
        assert result.channel_name == "Prime Video"
        assert result.content_id == "B0EXAMPL12"
        assert result.media_type == "movie"
        assert result.post_launch_key == "Select"


class TestConvertUrlToEcpCommandInvalidUrls:
    """Test invalid URLs that should return None."""

    def test_netflix_browse_page(self) -> None:
        assert convert_url_to_ecp_command("https://netflix.com/browse") is None

    def test_netflix_root_page(self) -> None:
        assert convert_url_to_ecp_command("https://netflix.com/") is None

    def test_netflix_search_page(self) -> None:
        assert convert_url_to_ecp_command("https://www.netflix.com/search?q=test") is None

    def test_disney_plus_browse_page(self) -> None:
        assert convert_url_to_ecp_command("https://disneyplus.com/browse") is None

    def test_disney_plus_root_page(self) -> None:
        assert convert_url_to_ecp_command("https://disneyplus.com/") is None

    def test_max_browse_page(self) -> None:
        assert convert_url_to_ecp_command("https://max.com/browse") is None

    def test_max_root_page(self) -> None:
        assert convert_url_to_ecp_command("https://max.com/") is None

    def test_amazon_browse_page(self) -> None:
        assert convert_url_to_ecp_command("https://amazon.com/browse") is None

    def test_amazon_root_page(self) -> None:
        assert convert_url_to_ecp_command("https://www.amazon.com/") is None

    def test_youtube_unsupported(self) -> None:
        assert convert_url_to_ecp_command("https://www.youtube.com/watch?v=abc123") is None

    def test_google_not_streaming(self) -> None:
        assert convert_url_to_ecp_command("https://www.google.com/") is None


class TestBuildPlaybackCommand:
    """Test playback command generation from extraction results."""

    def test_netflix_playback_command(self) -> None:
        extraction = ExtractionResult(
            channel_id="12",
            channel_name="Netflix",
            content_id="81444554",
            media_type="movie",
            post_launch_key="Play",
        )
        result = build_playback_command(extraction)

        assert result.type == "action_sequence"
        assert len(result.actions) == 3

        launch, wait, keypress = result.actions
        assert isinstance(launch, LaunchAction)
        assert launch.type == "launch"
        assert launch.channel_id == "12"
        assert launch.params == "contentId=81444554&mediaType=movie"

        assert isinstance(wait, WaitAction)
        assert wait.type == "wait"
        assert wait.milliseconds == 2000

        assert isinstance(keypress, KeypressAction)
        assert keypress.type == "keypress"
        assert keypress.key == "Play"
        assert keypress.count == 1

    def test_disney_plus_playback_command(self) -> None:
        extraction = ExtractionResult(
            channel_id="291097",
            channel_name="Disney+",
            content_id="f63db666-b097-4c61-99c1-b778de2d4ae1",
            media_type="movie",
            post_launch_key="Select",
        )
        result = build_playback_command(extraction)

        assert result.type == "action_sequence"
        launch, wait, keypress = result.actions

        assert launch.channel_id == "291097"
        assert launch.params == "contentId=f63db666-b097-4c61-99c1-b778de2d4ae1&mediaType=movie"
        assert wait.milliseconds == 2000
        assert keypress.key == "Select"

    def test_hbo_max_playback_command(self) -> None:
        extraction = ExtractionResult(
            channel_id="61322",
            channel_name="HBO Max",
            content_id="bd43b2a4-1639-4197-96d4-2ec14eb45e9e",
            media_type="movie",
            post_launch_key="Select",
        )
        result = build_playback_command(extraction)

        assert result.type == "action_sequence"
        launch, wait, keypress = result.actions

        assert launch.channel_id == "61322"
        assert launch.params == "contentId=bd43b2a4-1639-4197-96d4-2ec14eb45e9e&mediaType=movie"
        assert wait.milliseconds == 2000
        assert keypress.key == "Select"

    def test_prime_video_playback_command(self) -> None:
        extraction = ExtractionResult(
            channel_id="13",
            channel_name="Prime Video",
            content_id="B0DKTFF815",
            media_type="movie",
            post_launch_key="Select",
        )
        result = build_playback_command(extraction)

        assert result.type == "action_sequence"
        launch, wait, keypress = result.actions

        assert launch.channel_id == "13"
        assert launch.params == "contentId=B0DKTFF815&mediaType=movie"
        assert wait.milliseconds == 2000
        assert keypress.key == "Select"


class TestEndToEndUrlToPlayback:
    """Test full pipeline from URL to playback command."""

    @pytest.mark.parametrize(
        "url,expected_channel_id,expected_key",
        [
            ("https://www.netflix.com/watch/81444554", "12", "Play"),
            ("https://www.disneyplus.com/play/f63db666-b097-4c61-99c1-b778de2d4ae1", "291097", "Select"),
            ("https://www.max.com/video/watch/bd43b2a4-1639-4197-96d4-2ec14eb45e9e", "61322", "Select"),
            ("https://www.amazon.com/gp/video/detail/B0DKTFF815", "13", "Select"),
        ],
    )
    def test_url_to_playback_pipeline(
        self, url: str, expected_channel_id: str, expected_key: str
    ) -> None:
        extraction = convert_url_to_ecp_command(url)
        assert extraction is not None
        assert extraction.channel_id == expected_channel_id

        command = build_playback_command(extraction)
        assert command.type == "action_sequence"
        assert command.actions[0].channel_id == expected_channel_id
        assert command.actions[2].key == expected_key
