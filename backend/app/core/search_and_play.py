"""Content search and Roku launch pipeline.

search_content() — Brave Search → URL match → returns ALL matches across services.
launch_on_roku()  — POST to Roku ECP /launch/{channelId} with contentId.
"""

import json
import logging
from dataclasses import asdict, dataclass

import httpx

from .brave_search import BraveSearchClient, BraveSearchError
from .streaming import StreamingService, get_active_services, get_site_filters, match_url

logger = logging.getLogger("cueso.search_and_play")


@dataclass
class ContentMatch:
    """A single streaming service match with Roku launch details."""

    service_name: str
    channel_id: int
    content_id: str
    source_url: str
    title: str
    media_type: str


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
    """Build a search query from structured content fields."""
    parts = [title]
    if season is not None:
        parts.append(f"Season {season}")
    if episode is not None:
        parts.append(f"Episode {episode}")
    if episode_title:
        parts.append(episode_title)
    return " ".join(parts)


async def search_content(
    title: str,
    brave_client: BraveSearchClient,
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
    media_type: str | None = None,
    services: list[StreamingService] | None = None,
) -> ContentSearchResult:
    """Search streaming services for content and return all matches.

    Steps:
        1. Build search query from structured fields and append site: filters.
        2. Call Brave Search.
        3. Iterate through ALL results, match URLs to streaming services.
        4. Return every match (one per service, in priority order).

    Args:
        title: Content title (e.g. "Rick and Morty").
        brave_client: Configured BraveSearchClient instance.
        season: Optional season number.
        episode: Optional episode number.
        episode_title: Optional episode title for better search.
        media_type: Optional override for Roku mediaType param.
        services: Optional subset of streaming services to search.

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
        matched = match_url(result.url, services=target_services)
        if matched:
            service, content_id = matched
            if service.name in seen_services:
                continue
            seen_services.add(service.name)
            matches.append(
                ContentMatch(
                    service_name=service.name,
                    channel_id=service.roku_channel_id,
                    content_id=content_id,
                    source_url=result.url,
                    title=result.title,
                    media_type=media_type or service.default_media_type,
                )
            )
            logger.info(
                "Matched: service=%s content_id=%s url=%s",
                service.name,
                content_id,
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
) -> LaunchResult:
    """Launch content on Roku via ECP.

    Args:
        channel_id: Roku channel ID (e.g. 12 for Netflix).
        content_id: Content ID for deep linking.
        roku_base_url: Roku ECP base URL (e.g. "http://192.168.1.100:8060").
        http_client: Shared httpx client.
        media_type: Roku mediaType param (default "movie").

    Returns:
        LaunchResult with success status.
    """
    launch_url = f"{roku_base_url}/launch/{channel_id}"
    params = {"contentId": content_id, "mediaType": media_type}
    logger.info("Launching Roku: POST %s params=%s", launch_url, params)

    try:
        response = await http_client.post(launch_url, params=params, timeout=10.0)
    except httpx.RequestError as e:
        return LaunchResult(success=False, message=f"Roku connection failed: {e}")

    if response.status_code == 200:
        return LaunchResult(
            success=True,
            message=f"Launched channel {channel_id} with content ID {content_id}.",
            status_code=200,
        )
    else:
        return LaunchResult(
            success=False,
            message=f"Roku returned status {response.status_code}.",
            status_code=response.status_code,
        )
