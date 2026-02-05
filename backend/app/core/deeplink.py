"""Roku ECP URL-to-Playback conversion per roku-deeplink-spec.

This module implements the spec from https://github.com/jacob-meacham/roku-deeplink-spec
for converting streaming service URLs into Roku ECP playback commands.

Two core functions:
- convert_url_to_ecp_command(url) -> ExtractionResult | None
- build_playback_command(extraction) -> PlaybackCommand
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# --- Data Models ---


@dataclass(frozen=True)
class ExtractionResult:
    """Result of extracting channel info from a streaming URL."""

    channel_id: str
    channel_name: str
    content_id: str
    media_type: Literal["movie", "series"]
    post_launch_key: Literal["Play", "Select"]


@dataclass(frozen=True)
class LaunchAction:
    """Action to launch a Roku channel with deep link params."""

    type: Literal["launch"] = "launch"
    channel_id: str = ""
    params: str = ""


@dataclass(frozen=True)
class WaitAction:
    """Action to wait before next step."""

    type: Literal["wait"] = "wait"
    milliseconds: int = 2000


@dataclass(frozen=True)
class KeypressAction:
    """Action to press a remote key."""

    type: Literal["keypress"] = "keypress"
    key: str = ""
    count: int = 1


@dataclass(frozen=True)
class PlaybackCommand:
    """A sequence of actions to play content on Roku."""

    type: Literal["action_sequence"] = "action_sequence"
    actions: tuple[LaunchAction, WaitAction, KeypressAction] = ()  # type: ignore[assignment]


# --- Channel Catalog ---


@dataclass(frozen=True)
class Channel:
    """A streaming service channel configuration."""

    channel_id: str
    channel_name: str
    url_pattern: re.Pattern[str]
    post_launch_key: Literal["Play", "Select"]
    media_type_from_url: bool = False  # Only Netflix uses URL-based media type


# Channel definitions per spec
NETFLIX = Channel(
    channel_id="12",
    channel_name="Netflix",
    url_pattern=re.compile(r"netflix\.com/(?:watch|title)/(\d+)"),
    post_launch_key="Play",
    media_type_from_url=True,
)

DISNEY_PLUS = Channel(
    channel_id="291097",
    channel_name="Disney+",
    url_pattern=re.compile(r"disneyplus\.com/(?:(?:play|video)/|browse/entity-)([a-f0-9-]+)"),
    post_launch_key="Select",
)

HBO_MAX = Channel(
    channel_id="61322",
    channel_name="HBO Max",
    url_pattern=re.compile(r"(?:max\.com|hbomax\.com)/(?:(?:movies|series)/[^/]+/|(?:video/watch|play)/)([^/?]+)"),
    post_launch_key="Select",
)

PRIME_VIDEO = Channel(
    channel_id="13",
    channel_name="Prime Video",
    url_pattern=re.compile(r"(?:amazon\.com|primevideo\.com)/.*?/([B][A-Z0-9]{9})"),
    post_launch_key="Select",
)

# Channel catalog in match order
CHANNEL_CATALOG: tuple[Channel, ...] = (NETFLIX, DISNEY_PLUS, HBO_MAX, PRIME_VIDEO)


# --- Core Functions ---


def _determine_media_type(url: str, channel: Channel) -> Literal["movie", "series"]:
    """Determine media type from URL for Netflix, else return 'movie'."""
    if channel.media_type_from_url and "/title/" in url:
        return "series"
    return "movie"


def convert_url_to_ecp_command(url: str) -> ExtractionResult | None:
    """Convert a streaming URL to an ECP extraction result.

    Args:
        url: A URL string from a streaming service.

    Returns:
        ExtractionResult with channel info, or None if URL doesn't match.
    """
    for channel in CHANNEL_CATALOG:
        match = channel.url_pattern.search(url)
        if match:
            content_id = match.group(1)
            media_type = _determine_media_type(url, channel)
            return ExtractionResult(
                channel_id=channel.channel_id,
                channel_name=channel.channel_name,
                content_id=content_id,
                media_type=media_type,
                post_launch_key=channel.post_launch_key,
            )
    return None


def build_playback_command(extraction: ExtractionResult) -> PlaybackCommand:
    """Build a playback command from an extraction result.

    Args:
        extraction: An ExtractionResult from convert_url_to_ecp_command.

    Returns:
        PlaybackCommand with launch, wait, and keypress actions.
    """
    launch = LaunchAction(
        type="launch",
        channel_id=extraction.channel_id,
        params=f"contentId={extraction.content_id}&mediaType={extraction.media_type}",
    )
    wait = WaitAction(type="wait", milliseconds=2000)
    keypress = KeypressAction(
        type="keypress",
        key=extraction.post_launch_key,
        count=1,
    )
    return PlaybackCommand(type="action_sequence", actions=(launch, wait, keypress))
