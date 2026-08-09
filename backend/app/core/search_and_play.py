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
from .emby import EMBY_CHANNEL_ID, EmbyClient, EmbyError
from .streaming import StreamingService, UrlMatchResult, get_active_services, get_site_filters, match_url_full
from .tmdb import TMDBClient

logger = logging.getLogger("cueso.search_and_play")

# Browser-like UA for verification probes: Amazon serves the plain 200/404
# distinction to browsers but may bot-wall default library UAs.
VERIFY_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
VERIFY_TIMEOUT_SECONDS = 5.0
ORACLE_TIMEOUT_SECONDS = 2.0

_EMBY_MEDIA_TYPES = {"Movie": "movie", "Series": "series", "Episode": "episode"}


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
    brave_client: BraveSearchClient | None,
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
    media_type: str | None = None,
    services: list[StreamingService] | None = None,
    http_client: httpx.AsyncClient | None = None,
    emby_client: EmbyClient | None = None,
    tmdb_client: TMDBClient | None = None,
) -> ContentSearchResult:
    """Search the local Emby library and streaming services concurrently.

    Emby matches come first (the user's own server). An Emby failure degrades
    to streaming-only results; a missing Brave client degrades to Emby-only.

    Args:
        http_client: Client for verification probes (spec §11); omitting it
            skips probes (fail open).
        emby_client: Client for the local Emby library; omitting it skips
            Emby search.
        tmdb_client: Optional availability oracle; when given, matches whose
            service TMDB says cannot stream the title are dropped (fail open
            on any oracle failure).

    Returns:
        ContentSearchResult with all matches across sources.
    """
    base_query = build_search_query(title, season, episode, episode_title)

    oracle_task: asyncio.Task[set[str] | None] | None = None
    if tmdb_client is not None:
        oracle_task = asyncio.create_task(
            tmdb_client.get_streamable_services(title, tv_only=season is not None or episode is not None)
        )

    emby_matches, streaming = await asyncio.gather(
        _search_emby(emby_client, title, season, episode, media_type),
        _search_streaming(brave_client, base_query, media_type, services, http_client),
    )
    streaming_matches, streaming_failure = streaming

    streamable: set[str] | None = None
    if oracle_task is not None:
        try:
            streamable = await asyncio.wait_for(oracle_task, timeout=ORACLE_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("TMDB availability oracle failed (%s); returning matches unfiltered", e)

    # Emby is the user's own library, never a TMDB-tracked service — only
    # streaming matches are ever eligible for filtering.
    filter_notes: list[str] = []
    kept_streaming: list[ContentMatch] = streaming_matches
    if streamable is not None:
        kept_streaming = []
        for match in streaming_matches:
            if match.service_name in streamable:
                kept_streaming.append(match)
            else:
                filter_notes.append(f"filtered {match.service_name} (not streamable per TMDB)")
                logger.info("Filtered %s match %s: not streamable per TMDB", match.service_name, match.content_id)

    matches = emby_matches + kept_streaming
    if not matches:
        if filter_notes:
            message = "Found streaming URLs but none are playable: " + "; ".join(filter_notes)
        else:
            message = streaming_failure or f"No content found for: {base_query}"
        return ContentSearchResult(success=False, message=message, query=base_query, matches=[])

    service_names = [m.service_name for m in matches]
    message = f"Found content on {len(matches)} service(s): {', '.join(service_names)}"
    if filter_notes:
        message += " — " + "; ".join(filter_notes)
    return ContentSearchResult(success=True, message=message, query=base_query, matches=matches)


async def _search_emby(
    emby_client: EmbyClient | None,
    title: str,
    season: int | None,
    episode: int | None,
    media_type: str | None,
) -> list[ContentMatch]:
    """Search the local Emby library; failures degrade to no matches.

    A dead local server must never break streaming search.
    """
    if emby_client is None:
        return []
    try:
        items = await emby_client.search(title, season=season, episode=episode)
    except EmbyError as e:
        logger.warning("Emby search failed, continuing with streaming only: %s", e)
        return []

    matches: list[ContentMatch] = []
    for item in items:
        matches.append(
            ContentMatch(
                service_name="emby",
                channel_id=EMBY_CHANNEL_ID,
                content_id=item.item_id,
                source_url=f"{emby_client.server_url}/web/index.html#!/item?id={item.item_id}",
                title=item.name,
                media_type=media_type or _EMBY_MEDIA_TYPES.get(item.item_type, "movie"),
                post_launch_key=None,
                resume_position_ticks=item.resume_position_ticks,
            )
        )
        logger.info("Matched: service=emby content_id=%s", item.item_id)
    return matches


async def _search_streaming(
    brave_client: BraveSearchClient | None,
    base_query: str,
    media_type: str | None,
    services: list[StreamingService] | None,
    http_client: httpx.AsyncClient | None,
) -> tuple[list[ContentMatch], str | None]:
    """Search the web for streaming-service URLs.

    Returns (matches, failure_message); failure_message is None on success.
    """
    if brave_client is None:
        return [], "Brave Search is not configured."

    target_services = services or get_active_services()
    site_filter = get_site_filters(target_services)
    full_query = f"{base_query} {site_filter}"

    logger.info("Searching Brave: %s", full_query)
    try:
        results = await brave_client.search(full_query, count=10)
    except BraveSearchError as e:
        return [], f"Search failed: {e}"

    if not results:
        return [], f"No search results found for: {base_query}"

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
        return [], f"Found {len(results)} results but no streaming service URLs matched. Top URLs: {urls}"

    return matches, None


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
