"""Streaming service registry for content ID extraction and Roku deep linking.

Generated from spec library roku-deeplink v1.5.0 (speclib).
"""

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
    post_launch_key: str = "Select"  # Key to press after launch (Play for Netflix)
    media_type_from_url: bool = False  # Whether to detect media_type from the matched URL text
    # Per roku-deeplink-spec §4/§11: verification probe for search-sourced URLs.
    # GET of the filled template → 200 accept, 404 reject, anything else fail open.
    verify_url_template: str | None = None
    # URL substrings marking inherently-video pages that skip the probe.
    unambiguous_url_markers: tuple[str, ...] = ()

    def needs_verification(self, url: str) -> bool:
        """Whether a match from this URL must pass the verification probe.

        Only meaningful for services with a verify_url_template; URLs carrying
        an unambiguous marker (e.g. Amazon's /gp/video/ paths) skip the probe.
        """
        if self.verify_url_template is None:
            return False
        return not any(marker in url for marker in self.unambiguous_url_markers)

    def verification_url(self, content_id: str) -> str:
        """Build the probe URL for a content ID. Requires verify_url_template."""
        if self.verify_url_template is None:
            raise ValueError(f"{self.name} has no verification probe")
        return self.verify_url_template.format(content_id=content_id)

    def get_media_type(self, matched_text: str) -> str:
        """Determine media type from the regex-matched text or return default.

        Must be given the matched text (regex group 0), not the full URL: the
        other path segment can appear elsewhere in the URL (e.g. a query
        parameter), but exactly one of /watch/ or /title/ appears in a match.
        """
        if self.media_type_from_url and "/title/" in matched_text:
            return "series"
        return self.default_media_type


# --- Service Definitions ---

NETFLIX = StreamingService(
    name="netflix",
    roku_channel_id=12,
    domains=("netflix.com",),
    url_patterns=(
        # Per roku-deeplink-spec: netflix\.com/(?:\w{2}(?:-\w{2})?/)?(?:watch|title)/(\d+)
        # Optional locale prefix (/us/, /en-gb/) common in search engine results
        re.compile(r"netflix\.com/(?:\w{2}(?:-\w{2})?/)?(?:watch|title)/(\d+)"),
    ),
    post_launch_key="Play",
    media_type_from_url=True,  # /watch/ = movie, /title/ = series
)

AMAZON_PRIME = StreamingService(
    name="amazon_prime",
    roku_channel_id=13,
    domains=("amazon.com", "primevideo.com"),
    url_patterns=(
        # Per roku-deeplink-spec: (?:amazon\.com|primevideo\.com)/.*?/([B][A-Z0-9]{9})
        # ASIN format: B + 9 alphanumeric characters
        re.compile(r"(?:amazon\.com|primevideo\.com)/.*?/(B[A-Z0-9]{9})"),
    ),
    # Retail products (DVDs, Blu-rays) share the /dp/{ASIN} URL shape with Prime
    # Video titles; the probe is the only reliable discriminator (spec §4).
    verify_url_template="https://www.primevideo.com/detail/{content_id}",
    unambiguous_url_markers=("/gp/video/", "primevideo.com"),
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
        # Per roku-deeplink-spec: disneyplus\.com/(?:(?:play|video)/|browse/entity-)([a-f0-9-]+)
        # Supports /play/, /video/, and /browse/entity- paths with UUID format
        re.compile(r"disneyplus\.com/(?:(?:play|video)/|browse/entity-)([a-f0-9-]+)"),
    ),
)

MAX = StreamingService(
    name="max",
    roku_channel_id=61322,
    domains=("max.com", "hbomax.com"),
    url_patterns=(
        # Per roku-deeplink-spec:
        # (?:max\.com|hbomax\.com)/(?:(?:movies|series|shows)/[^/]+/(?:s\d+/)?|(?:video/watch|play)/)([^/?]+)
        # Supports /movies/{title}/, /series/{title}/, /shows/{title}/ (current
        # site scheme, with an optional season segment like s1/), /video/watch/,
        # and /play/ paths
        re.compile(
            r"(?:max\.com|hbomax\.com)/(?:(?:movies|series|shows)/[^/]+/(?:s\d+/)?|(?:video/watch|play)/)([^/?]+)"
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


@dataclass
class UrlMatchResult:
    """Result of matching a URL to a streaming service."""

    service: StreamingService
    content_id: str
    media_type: str
    post_launch_key: str


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


def match_url_full(url: str, services: list[StreamingService] | None = None) -> UrlMatchResult | None:
    """Match a URL to a streaming service with full extraction details.

    Tries services in priority order, returns the first match with
    media_type and post_launch_key.

    Returns:
        UrlMatchResult or None if no match.
    """
    for service in services or get_active_services():
        for pattern in service.url_patterns:
            m = pattern.search(url)
            if m:
                return UrlMatchResult(
                    service=service,
                    content_id=m.group(1),
                    media_type=service.get_media_type(m.group(0)),
                    post_launch_key=service.post_launch_key,
                )
    return None


def get_site_filters(services: list[StreamingService] | None = None) -> str:
    """Build a Brave Search site: filter string for the given services.

    Example: "site:netflix.com OR site:hulu.com OR site:disneyplus.com"
    """
    targets = services or get_active_services()
    parts = [f"site:{domain}" for svc in targets for domain in svc.domains]
    return " OR ".join(parts)
