"""Tests for the streaming service registry and URL matching."""

from unittest.mock import patch

from app.core.streaming import (
    AMAZON_PRIME,
    APPLE_TV_PLUS,
    DISNEY_PLUS,
    HULU,
    MAX,
    NETFLIX,
    SERVICE_REGISTRY,
    get_active_services,
    get_site_filters,
    match_url,
)


class TestMatchUrlNetflix:
    def test_title_url(self) -> None:
        result = match_url("https://www.netflix.com/title/81231974")
        assert result is not None
        service, content_id = result
        assert service is NETFLIX
        assert content_id == "81231974"

    def test_watch_url(self) -> None:
        result = match_url("https://www.netflix.com/watch/81231974")
        assert result is not None
        service, content_id = result
        assert service is NETFLIX
        assert content_id == "81231974"

    def test_regional_url(self) -> None:
        result = match_url("https://www.netflix.com/gb/title/81231974")
        assert result is not None
        service, content_id = result
        assert service is NETFLIX
        assert content_id == "81231974"

    def test_regional_with_subcode(self) -> None:
        result = match_url("https://www.netflix.com/en-US/title/81231974")
        assert result is not None
        _, content_id = result
        assert content_id == "81231974"


class TestMatchUrlAmazon:
    def test_video_detail_url(self) -> None:
        result = match_url("https://www.amazon.com/gp/video/detail/B07ZPDN57Q")
        assert result is not None
        service, content_id = result
        assert service is AMAZON_PRIME
        assert content_id == "B07ZPDN57Q"

    def test_dp_url(self) -> None:
        result = match_url("https://www.amazon.com/dp/B07ZPDN57Q")
        assert result is not None
        service, content_id = result
        assert service is AMAZON_PRIME
        assert content_id == "B07ZPDN57Q"

    def test_primevideo_url(self) -> None:
        result = match_url("https://www.primevideo.com/detail/Severance/0FDIT8JCDQ4JC56IXP5UN4UM1W")
        assert result is not None
        service, content_id = result
        assert service is AMAZON_PRIME
        assert content_id == "0FDIT8JCDQ4JC56IXP5UN4UM1W"

    def test_primevideo_regional_url(self) -> None:
        result = match_url("https://www.primevideo.com/-/tr/detail/Severance/0FDIT8JCDQ4JC56IXP5UN4UM1W")
        assert result is not None
        service, content_id = result
        assert service is AMAZON_PRIME
        assert content_id == "0FDIT8JCDQ4JC56IXP5UN4UM1W"

    def test_long_dp_url(self) -> None:
        result = match_url("https://www.amazon.com/Severance-Season-2/dp/B0DSQ72YH5")
        assert result is not None
        service, content_id = result
        assert service is AMAZON_PRIME
        assert content_id == "B0DSQ72YH5"


class TestMatchUrlHulu:
    def test_series_url(self) -> None:
        result = match_url("https://www.hulu.com/series/the-bear-565d8976-9e52-4f30-a6f5-a47e7fe1abd4", services=[HULU])
        assert result is not None
        service, content_id = result
        assert service is HULU
        assert content_id == "565d8976-9e52-4f30-a6f5-a47e7fe1abd4"

    def test_watch_url(self) -> None:
        result = match_url("https://www.hulu.com/watch/565d8976-9e52-4f30-a6f5-a47e7fe1abd4", services=[HULU])
        assert result is not None
        service, content_id = result
        assert service is HULU
        assert content_id == "565d8976-9e52-4f30-a6f5-a47e7fe1abd4"

    def test_movie_url(self) -> None:
        result = match_url(
            "https://www.hulu.com/movie/some-movie-12345678-1234-1234-1234-123456789abc", services=[HULU]
        )
        assert result is not None
        service, content_id = result
        assert service is HULU
        assert content_id == "12345678-1234-1234-1234-123456789abc"


class TestMatchUrlDisneyPlus:
    def test_movies_url(self) -> None:
        result = match_url("https://www.disneyplus.com/movies/encanto/abc123def456")
        assert result is not None
        service, content_id = result
        assert service is DISNEY_PLUS
        assert content_id == "abc123def456"

    def test_series_url(self) -> None:
        result = match_url("https://www.disneyplus.com/series/the-mandalorian/3jLIGMDYINqD")
        assert result is not None
        service, content_id = result
        assert service is DISNEY_PLUS
        assert content_id == "3jLIGMDYINqD"

    def test_regional_url(self) -> None:
        result = match_url("https://www.disneyplus.com/en-GB/movies/encanto/abc123def456")
        assert result is not None
        _, content_id = result
        assert content_id == "abc123def456"


class TestMatchUrlMax:
    def test_show_uuid_url(self) -> None:
        result = match_url("https://play.max.com/show/9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8")
        assert result is not None
        service, content_id = result
        assert service is MAX
        assert content_id == "9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8"

    def test_movie_uuid_url(self) -> None:
        result = match_url("https://max.com/movie/9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8")
        assert result is not None
        service, content_id = result
        assert service is MAX
        assert content_id == "9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8"

    def test_episode_uuid_url(self) -> None:
        result = match_url("https://play.max.com/episode/9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8")
        assert result is not None
        service, content_id = result
        assert service is MAX
        assert content_id == "9ec0e921-1b4a-4c2e-8e5d-f3a4b5c6d7e8"


class TestMatchUrlAppleTVPlus:
    def test_show_url(self) -> None:
        result = match_url("https://tv.apple.com/us/show/severance/umc.cmc.1srk2goyh2q2zdxcx605w8vtx")
        assert result is not None
        service, content_id = result
        assert service is APPLE_TV_PLUS
        assert content_id == "umc.cmc.1srk2goyh2q2zdxcx605w8vtx"

    def test_movie_url(self) -> None:
        result = match_url("https://tv.apple.com/us/movie/killers-of-the-flower-moon/umc.cmc.5x1fg9gl9mwn7qzd3s6ztph5p")
        assert result is not None
        service, content_id = result
        assert service is APPLE_TV_PLUS
        assert content_id == "umc.cmc.5x1fg9gl9mwn7qzd3s6ztph5p"

    def test_no_region_url(self) -> None:
        result = match_url("https://tv.apple.com/show/severance/umc.cmc.1srk2goyh2q2zdxcx605w8vtx")
        assert result is not None
        _, content_id = result
        assert content_id == "umc.cmc.1srk2goyh2q2zdxcx605w8vtx"


class TestMatchUrlNoMatch:
    def test_google_url(self) -> None:
        assert match_url("https://www.google.com/search?q=the+bear") is None

    def test_imdb_url(self) -> None:
        assert match_url("https://www.imdb.com/title/tt1234567/") is None

    def test_empty_string(self) -> None:
        assert match_url("") is None

    def test_partial_netflix_url(self) -> None:
        assert match_url("https://www.netflix.com/browse") is None


class TestServiceRegistry:
    def test_all_services_registered(self) -> None:
        assert len(SERVICE_REGISTRY) == 6
        assert "netflix" in SERVICE_REGISTRY
        assert "hulu" in SERVICE_REGISTRY
        assert "disney_plus" in SERVICE_REGISTRY
        assert "max" in SERVICE_REGISTRY
        assert "apple_tv_plus" in SERVICE_REGISTRY
        assert "amazon_prime" in SERVICE_REGISTRY

    def test_registry_maps_to_correct_instances(self) -> None:
        assert SERVICE_REGISTRY["netflix"] is NETFLIX
        assert SERVICE_REGISTRY["amazon_prime"] is AMAZON_PRIME
        assert SERVICE_REGISTRY["apple_tv_plus"] is APPLE_TV_PLUS


class TestGetActiveServices:
    def test_default_priority(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.streaming = ["netflix", "hulu", "disney_plus", "max", "apple_tv_plus", "amazon_prime"]
            services = get_active_services()
            assert len(services) == 6
            assert services[0] is NETFLIX
            assert services[-1] is AMAZON_PRIME

    def test_custom_priority(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.streaming = ["hulu", "netflix"]
            services = get_active_services()
            assert services == [HULU, NETFLIX]

    def test_unknown_service_ignored(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.streaming = ["netflix", "nonexistent", "hulu"]
            services = get_active_services()
            assert services == [NETFLIX, HULU]

    def test_empty_priority_falls_back(self) -> None:
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.streaming = []
            services = get_active_services()
            # Falls back to default when result would be empty
            assert len(services) == 6
            assert services[0] is NETFLIX


class TestGetSiteFilters:
    def test_all_services(self) -> None:
        all_services = [NETFLIX, HULU, DISNEY_PLUS, MAX, APPLE_TV_PLUS, AMAZON_PRIME]
        result = get_site_filters(all_services)
        assert "site:netflix.com" in result
        assert "site:hulu.com" in result
        assert "site:disneyplus.com" in result
        assert "site:max.com" in result
        assert "site:tv.apple.com" in result
        assert "site:amazon.com" in result
        assert "site:primevideo.com" in result
        assert " OR " in result

    def test_subset(self) -> None:
        result = get_site_filters([NETFLIX, HULU])
        assert "site:netflix.com" in result
        assert "site:hulu.com" in result
        assert "site:amazon.com" not in result

    def test_single_service(self) -> None:
        result = get_site_filters([NETFLIX])
        assert result == "site:netflix.com"
        assert " OR " not in result


class TestStreamingServiceImmutable:
    def test_frozen(self) -> None:
        try:
            NETFLIX.name = "something_else"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass
