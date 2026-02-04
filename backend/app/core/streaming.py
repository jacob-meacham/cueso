"""Streaming service registry for content ID extraction and Roku deep linking."""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("cueso.streaming")


@dataclass(frozen=True)
class StreamingService:
    """A streaming service with Roku deep link support."""

    name: str
    roku_channel_id: int
    domains: tuple[str, ...]
    url_patterns: tuple[re.Pattern[str], ...] = field(repr=False)
    default_media_type: str = "movie"


# --- Service Definitions ---

NETFLIX = StreamingService(
    name="netflix",
    roku_channel_id=12,
    domains=("netflix.com",),
    url_patterns=(
        re.compile(r"netflix\.com/(?:\w{2}(?:-\w{2})?/)?title/(\d+)"),
        re.compile(r"netflix\.com/(?:\w{2}(?:-\w{2})?/)?watch/(\d+)"),
    ),
)

AMAZON_PRIME = StreamingService(
    name="amazon_prime",
    roku_channel_id=13,
    domains=("amazon.com", "primevideo.com"),
    url_patterns=(
        re.compile(r"amazon\.com/gp/video/detail/([A-Z0-9]{10,})"),
        re.compile(r"amazon\.com/(?:[^/]+/)?dp/([A-Z0-9]{10,})"),
        re.compile(r"primevideo\.com/(?:[a-z-]+/)*detail/(?:[^/]+/)?([A-Z0-9]{10,})"),
    ),
)

HULU = StreamingService(
    name="hulu",
    roku_channel_id=2285,
    domains=("hulu.com",),
    url_patterns=(
        re.compile(
            r"hulu\.com/(?:series|watch|movie)/(?:[a-z0-9-]+-)?("
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        ),
    ),
)

DISNEY_PLUS = StreamingService(
    name="disney_plus",
    roku_channel_id=291097,
    domains=("disneyplus.com",),
    url_patterns=(
        re.compile(
            r"disneyplus\.com/(?:\w{2}(?:-\w{2})?/)?"
            r"(?:movies|series|video)/[^/]+/([0-9A-Za-z]{12})"
        ),
    ),
)

MAX = StreamingService(
    name="max",
    roku_channel_id=61322,
    domains=("max.com", "play.max.com"),
    url_patterns=(
        re.compile(
            r"(?:play\.)?max\.com/(?:movie|show|episode|season|video)/"
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        ),
    ),
)

APPLE_TV_PLUS = StreamingService(
    name="apple_tv_plus",
    roku_channel_id=551012,
    domains=("tv.apple.com",),
    url_patterns=(re.compile(r"tv\.apple\.com/(?:\w{2}/)?(?:show|movie|episode)/[^/]+/(umc\.cmc\.[a-z0-9]+)"),),
)

# --- Registry and config-driven priority ---

SERVICE_REGISTRY: dict[str, StreamingService] = {
    svc.name: svc for svc in [NETFLIX, HULU, DISNEY_PLUS, MAX, APPLE_TV_PLUS, AMAZON_PRIME]
}

_DEFAULT_PRIORITY: list[StreamingService] = [
    NETFLIX,
    HULU,
    DISNEY_PLUS,
    MAX,
    APPLE_TV_PLUS,
    AMAZON_PRIME,
]


def get_active_services() -> list[StreamingService]:
    """Return streaming services in the priority order defined by config.

    Services not listed in config are excluded. Falls back to the default
    list if config is unavailable or the resulting list would be empty.
    """
    try:
        from .config import settings

        priority_names = settings.streaming
    except Exception:
        return list(_DEFAULT_PRIORITY)

    result: list[StreamingService] = []
    for name in priority_names:
        svc = SERVICE_REGISTRY.get(name)
        if svc is not None:
            result.append(svc)
        else:
            logger.warning("Unknown streaming service in config: %s", name)
    return result if result else list(_DEFAULT_PRIORITY)


# Module-level convenience — uses config-driven priority
STREAMING_SERVICES: list[StreamingService] = get_active_services()


def match_url(url: str, services: list[StreamingService] | None = None) -> tuple[StreamingService, str] | None:
    """Match a URL to a streaming service and extract its content ID.

    Tries services in priority order, returns the first match.

    Returns:
        (service, content_id) or None if no match.
    """
    for service in services or get_active_services():
        for pattern in service.url_patterns:
            m = pattern.search(url)
            if m:
                return service, m.group(1)
    return None


def get_site_filters(services: list[StreamingService] | None = None) -> str:
    """Build a Brave Search site: filter string for the given services.

    Example: "site:netflix.com OR site:hulu.com OR site:disneyplus.com"
    """
    targets = services or get_active_services()
    parts = [f"site:{domain}" for svc in targets for domain in svc.domains]
    return " OR ".join(parts)
