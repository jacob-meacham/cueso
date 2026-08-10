"""Fixture-driven conformance tests for cueso's LIVE Roku deep-link path.

These validate the roku-deeplink spec (v1.5.0) against the code that actually
runs in production -- NOT a standalone module. The live path is:

    api/chat.py -> llm/tool_executor.py -> search_and_play.py -> streaming.py

Spec Function 1 ``convert_url_to_ecp_command(url) -> extraction_result | null``
    is implemented live by ``streaming.match_url_full(url)``, which yields a
    ``UrlMatchResult(service, content_id, media_type, post_launch_key)`` or None.

Spec Function 2 ``build_playback_command(descriptor) -> action_sequence``
    is implemented live by ``search_and_play.launch_on_roku(...)``, which
    *executes* the launch -> wait 2000ms -> keypress sequence over Roku ECP.
    We drive it with a mocked HTTP client + patched sleep and assert the emitted
    sequence matches the fixture's action list.

Fixtures come from the spec's canonical ``test_fixtures.json`` (materialized by
speclib at v1.5.0 into ``tests/roku_deeplink_fixtures.json``).

The spec's Prime Video verification probe (§4) and web-search sourcing rules
(§11) are live-HTTP concerns, not fixture-testable; they are covered by unit
tests in ``test_search_and_play.py`` instead.

Name adapter: cueso identifies services by internal names (e.g. ``apple_tv_plus``)
while the spec identifies channels by ``channel_id`` / ``channel_name``.
``SERVICE_TO_SPEC_CHANNEL`` maps between the two so the fixtures (keyed on the
spec's identity) can validate cueso's live output. The ``channel_id`` assertion is
anchored to live code (``service.roku_channel_id``); the adapter's value must
agree with it, so the mapping cannot silently drift.

Emby (channel_id 44191) is launch-only and addressed by descriptor, not URL:
its 2 playback fixtures run against ``launch_on_roku`` (single launch POST,
PlayNow params, optional StartPositionTicks, no wait/keypress); it is never
produced by URL matching. YouTube (channel_id 837, added in spec v1.4.1,
selected here since the sync of 2026-08-09) is the first launch-only URL
channel: a single launch with no wait/keypress.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.search_and_play import launch_on_roku
from app.core.streaming import (
    AMAZON_PRIME,
    APPLE_TV_PLUS,
    DISNEY_PLUS,
    HULU,
    MAX,
    NETFLIX,
    YOUTUBE,
    match_url_full,
)

FIXTURES: dict[str, Any] = json.loads((Path(__file__).parent / "roku_deeplink_fixtures.json").read_text())

# All six channels cueso supports, in spec priority order. Passed explicitly so
# the tests do not depend on config.yml, which may disable a service (it disables
# Hulu in this repo) and would otherwise skip that channel's fixtures.
ALL_SERVICES = [NETFLIX, HULU, DISNEY_PLUS, MAX, APPLE_TV_PLUS, AMAZON_PRIME, YOUTUBE]

# cueso internal service name -> (spec channel_id, spec channel_name).
SERVICE_TO_SPEC_CHANNEL: dict[str, tuple[str, str]] = {
    "netflix": ("12", "Netflix"),
    "amazon_prime": ("13", "Prime Video"),
    "hulu": ("2285", "Hulu"),
    "disney_plus": ("291097", "Disney+"),
    "max": ("61322", "HBO Max"),
    "apple_tv_plus": ("551012", "Apple TV+"),
    "youtube": ("837", "YouTube"),
}

ROKU_BASE_URL = "http://192.168.1.100:8060"


class TestSpecFunction1ValidUrls:
    """Function 1 (live: streaming.match_url_full) reproduces every valid_urls case."""

    @pytest.mark.parametrize(
        "case",
        FIXTURES["valid_urls"],
        ids=[c["url"] for c in FIXTURES["valid_urls"]],
    )
    def test_valid_url(self, case: dict[str, Any]) -> None:
        expected = case["expected"]
        result = match_url_full(case["url"], services=ALL_SERVICES)

        assert result is not None, f"live code failed to match {case['url']!r}"

        # Map cueso's internal service identity to the spec's channel identity.
        channel_id, channel_name = SERVICE_TO_SPEC_CHANNEL[result.service.name]

        # channel_id comes from live code (roku_channel_id); the adapter value and
        # the fixture must all agree, proving the mapping is faithful.
        assert str(result.service.roku_channel_id) == channel_id == expected["channel_id"]
        assert channel_name == expected["channel_name"]
        assert result.content_id == expected["content_id"]
        assert result.media_type == expected["media_type"]
        # Launch-only channels (YouTube) omit post_launch_key in the fixture;
        # the live result must agree by carrying None.
        assert result.post_launch_key == expected.get("post_launch_key")


class TestSpecFunction1InvalidUrls:
    """Function 1 returns None (null) for every invalid_urls case."""

    @pytest.mark.parametrize(
        "case",
        FIXTURES["invalid_urls"],
        ids=[c["url"] for c in FIXTURES["invalid_urls"]],
    )
    def test_invalid_url(self, case: dict[str, Any]) -> None:
        assert match_url_full(case["url"], services=ALL_SERVICES) is None


_PLAYBACK_CASES: list[dict[str, Any]] = FIXTURES["playback_commands"]


class TestSpecFunction2PlaybackCommands:
    """Function 2 (live: search_and_play.launch_on_roku) emits the spec's action
    sequence for every playback fixture: launch -> wait 2000ms -> keypress for
    URL channels, a single launch for Emby (launch-only)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case",
        _PLAYBACK_CASES,
        ids=[f"{c['input']['channel_name']}-{i}" for i, c in enumerate(_PLAYBACK_CASES)],
    )
    async def test_playback_sequence(self, case: dict[str, Any], mock_http_client: AsyncMock) -> None:
        descriptor = case["input"]
        expected_actions = case["expected"]["actions"]

        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await launch_on_roku(
                channel_id=int(descriptor["channel_id"]),
                content_id=descriptor["content_id"],
                roku_base_url=ROKU_BASE_URL,
                http_client=mock_http_client,
                media_type=descriptor.get("media_type", "movie"),
                post_launch_key=descriptor.get("post_launch_key"),
                resume_position_ticks=descriptor.get("resume_position_ticks"),
            )

        assert result.success is True
        assert case["expected"]["type"] == "action_sequence"
        calls = mock_http_client.post.call_args_list

        # Action 1 is always the launch, with exact spec params (order matters).
        launch_action = expected_actions[0]
        assert launch_action["type"] == "launch"
        assert f"/launch/{launch_action['channel_id']}" in calls[0].args[0]
        params = calls[0].kwargs["params"]
        serialized = "&".join(f"{k}={v}" for k, v in params.items())
        assert serialized == launch_action["params"]

        if len(expected_actions) == 1:
            # Launch-only channel (Emby): no wait, no keypress.
            assert len(calls) == 1
            mock_sleep.assert_not_awaited()
        else:
            assert [a["type"] for a in expected_actions] == ["launch", "wait", "keypress"]
            _, wait_action, keypress_action = expected_actions
            assert len(calls) == 2
            mock_sleep.assert_awaited_once_with(wait_action["milliseconds"] / 1000)
            assert f"/keypress/{keypress_action['key']}" in calls[1].args[0]
            assert keypress_action["count"] == 1
