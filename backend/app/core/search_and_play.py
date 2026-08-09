"""Content search and Roku launch pipeline.

search_content() — Brave Search → URL match → verify → returns ALL matches across services.
launch_on_roku()  — Execute action sequence: launch → wait 2000ms → keypress.

Generated from spec library roku-deeplink v1.5.0 (speclib).
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass

import httpx

from .brave_search import BraveSearchClient, BraveSearchError
from .emby import EMBY_CHANNEL_ID
from .streaming import StreamingService, UrlMatchResult, get_active_services, get_site_filters, match_url_full

logger = logging.getLogger("cueso.search_and_play")

# Browser-like UA for verification probes: Amazon serves the plain 200/404
# distinction to browsers but may bot-wall default library UAs.
VERIFY_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
VERIFY_TIMEOUT_SECONDS = 5.0


@dataclass
class ContentMatch:
    """A single streaming service match with Roku launch details."""

    service_name: str
    channel_id: int
    content_id: str
    source_url: str
    title: str
    media_type: str
    post_launch_key: str | None = "Select"  # Key to press after launch; None = launch-only (Emby)
    resume_position_ticks: int | None = None  # Emby resume position, when partially watched


@dataclass
class ContentSearchResult:
    """Result of searching for content across streaming services."""

    success: bool
    message: str
    query: str
    matches: list[ContentMatch]

    def to_tool_result(self) -> str:
        """Serialize to a JSON string suitable for returning as an LLM tool result."""
        return json.dumps(asdict(self), indent=2)


@dataclass
class LaunchResult:
    """Result of launching content on Roku."""

    success: bool
    message: str
    status_code: int | None = None


def build_search_query(
    title: str,
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
) -> str:
    """Build a search query from structured content fields.

    Per roku-deeplink-spec §11, the query leads with "watch": bare-title
    queries rank retail and placeholder pages above streaming pages.
    """
    parts = ["watch", title]
    if season is not None:
        parts.append(f"Season {season}")
    if episode is not None:
        parts.append(f"Episode {episode}")
    if episode_title:
        parts.append(episode_title)
    return " ".join(parts)


async def _verify_match(
    matched: UrlMatchResult,
    source_url: str,
    http_client: httpx.AsyncClient | None,
) -> bool:
    """Run the service's verification probe on a matched URL, if required.

    Per roku-deeplink-spec §4 (Prime Video): GET the probe URL — 200 accepts,
    404 rejects, anything else (including no client to probe with) fails open
    so a transient error never blocks a legitimate launch.
    """
    service = matched.service
    if not service.needs_verification(source_url):
        return True
    if http_client is None:
        logger.debug("No HTTP client for verification probe; accepting %s unverified", matched.content_id)
        return True

    probe_url = service.verification_url(matched.content_id)
    try:
        response = await http_client.get(
            probe_url,
            headers={"User-Agent": VERIFY_USER_AGENT},
            follow_redirects=True,
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        logger.warning("Verification probe failed for %s (%s); accepting unverified", probe_url, e)
        return True

    if response.status_code == 404:
        return False
    if response.status_code != 200:
        logger.warning("Verification probe returned %s for %s; accepting unverified", response.status_code, probe_url)
    return True


async def search_content(
    title: str,
    brave_client: BraveSearchClient,
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
    media_type: str | None = None,
    services: list[StreamingService] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ContentSearchResult:
    """Search streaming services for content and return all matches.

    Steps:
        1. Build search query from structured fields and append site: filters.
        2. Call Brave Search.
        3. Iterate through ALL results, match URLs to streaming services,
           verifying ambiguous matches (spec §4/§11) when a client is given.
        4. Return every verified match (one per service, in priority order).

    Args:
        title: Content title (e.g. "Rick and Morty").
        brave_client: Configured BraveSearchClient instance.
        season: Optional season number.
        episode: Optional episode number.
        episode_title: Optional episode title for better search.
        media_type: Optional override for Roku mediaType param.
        services: Optional subset of streaming services to search.
        http_client: Client for verification probes; omitting it skips probes
            (fail open).

    Returns:
        ContentSearchResult with all matches across services.
    """
    target_services = services or get_active_services()
    base_query = build_search_query(title, season, episode, episode_title)
    site_filter = get_site_filters(target_services)
    full_query = f"{base_query} {site_filter}"

    logger.info("Searching Brave: %s", full_query)
    try:
        results = await brave_client.search(full_query, count=10)
    except BraveSearchError as e:
        return ContentSearchResult(success=False, message=f"Search failed: {e}", query=base_query, matches=[])

    if not results:
        return ContentSearchResult(
            success=False,
            message=f"No search results found for: {base_query}",
            query=base_query,
            matches=[],
        )

    # Collect all matches, one per service (first URL wins for that service)
    matches: list[ContentMatch] = []
    seen_services: set[str] = set()

    for result in results:
        matched = match_url_full(result.url, services=target_services)
        if matched:
            if matched.service.name in seen_services:
                continue
            # Spec §11: a rejected candidate must not satisfy or block its
            # service — keep scanning so a later legit URL can claim it.
            if not await _verify_match(matched, result.url, http_client):
                logger.info(
                    "Rejected %s candidate %s (verification probe 404): %s",
                    matched.service.name,
                    matched.content_id,
                    result.url,
                )
                continue
            seen_services.add(matched.service.name)
            matches.append(
                ContentMatch(
                    service_name=matched.service.name,
                    channel_id=matched.service.roku_channel_id,
                    content_id=matched.content_id,
                    source_url=result.url,
                    title=result.title,
                    media_type=media_type or matched.media_type,
                    post_launch_key=matched.post_launch_key,
                )
            )
            logger.info(
                "Matched: service=%s content_id=%s url=%s",
                matched.service.name,
                matched.content_id,
                result.url,
            )

    if not matches:
        urls = [r.url for r in results[:5]]
        return ContentSearchResult(
            success=False,
            message=f"Found {len(results)} results but no streaming service URLs matched. Top URLs: {urls}",
            query=base_query,
            matches=[],
        )

    service_names = [m.service_name for m in matches]
    return ContentSearchResult(
        success=True,
        message=f"Found content on {len(matches)} service(s): {', '.join(service_names)}",
        query=base_query,
        matches=matches,
    )


async def launch_on_roku(
    channel_id: int,
    content_id: str,
    roku_base_url: str,
    http_client: httpx.AsyncClient,
    media_type: str = "movie",
    post_launch_key: str | None = "Select",
    resume_position_ticks: int | None = None,
) -> LaunchResult:
    """Launch content on Roku via ECP using the roku-deeplink action sequence.

    URL channels: POST /launch/{channel_id}?contentId={id}&mediaType={type},
    wait 2000ms, POST /keypress/{key}.
    Emby (channel 44191) is launch-only: POST /launch/44191?Command=PlayNow
    &ItemIds={id}[&StartPositionTicks={ticks}] and nothing else. A None
    post_launch_key makes any launch launch-only (spec Function 2 semantics).

    Args:
        channel_id: Roku channel ID (e.g. 12 for Netflix).
        content_id: Content ID for deep linking.
        roku_base_url: Roku ECP base URL (e.g. "http://192.168.1.100:8060").
        http_client: Shared httpx client.
        media_type: Roku mediaType param (default "movie").
        post_launch_key: Key to press after launch (default "Select"); None means launch-only.
        resume_position_ticks: Emby resume position, when partially watched.

    Returns:
        LaunchResult with success status.
    """
    # Step 1: Launch the channel with deep link params
    launch_url = f"{roku_base_url}/launch/{channel_id}"
    # Emby is launch-only per roku-deeplink spec, whatever the caller passed.
    if channel_id == EMBY_CHANNEL_ID:
        params: dict[str, str] = {"Command": "PlayNow", "ItemIds": content_id}
        if resume_position_ticks is not None:
            params["StartPositionTicks"] = str(resume_position_ticks)
        post_launch_key = None
    else:
        params = {"contentId": content_id, "mediaType": media_type}
    logger.info("Launching Roku: POST %s params=%s", launch_url, params)

    try:
        response = await http_client.post(launch_url, params=params, timeout=10.0)
    except httpx.RequestError as e:
        return LaunchResult(success=False, message=f"Roku connection failed: {e}")

    if response.status_code != 200:
        return LaunchResult(
            success=False,
            message=f"Roku launch returned status {response.status_code}.",
            status_code=response.status_code,
        )

    if post_launch_key is None:
        return LaunchResult(
            success=True,
            message=f"Launched channel {channel_id} with content ID {content_id} (launch-only).",
            status_code=200,
        )

    # Step 2: Wait 2000ms for app to load
    logger.info("Waiting 2000ms for app to load...")
    await asyncio.sleep(2.0)

    # Step 3: Press the post-launch key
    keypress_url = f"{roku_base_url}/keypress/{post_launch_key}"
    logger.info("Sending keypress: POST %s", keypress_url)

    try:
        key_response = await http_client.post(keypress_url, timeout=10.0)
    except httpx.RequestError as e:
        return LaunchResult(
            success=False,
            message=f"Launch succeeded but keypress failed: {e}",
            status_code=200,
        )

    if key_response.status_code != 200:
        return LaunchResult(
            success=False,
            message=f"Launch succeeded but keypress returned status {key_response.status_code}.",
            status_code=key_response.status_code,
        )

    return LaunchResult(
        success=True,
        message=f"Launched channel {channel_id} with content ID {content_id}, pressed {post_launch_key}.",
        status_code=200,
    )
