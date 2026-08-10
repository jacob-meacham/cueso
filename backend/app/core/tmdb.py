"""TMDB watch-providers availability oracle.

Answers "which cueso services can actually play this title?" using TMDB's
JustWatch-licensed watch-providers data. Used by search_content to drop
matches for services that cannot stream a title (e.g. Netflix placeholder
pages). Design: docs/superpowers/specs/2026-08-08-tmdb-availability-design.md
"""

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger("cueso.tmdb")

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
MAX_CANDIDATES = 3

# TMDB provider IDs are stable; several map to one cueso service. Rent/buy
# stores count as playable: the Roku deep link opens the title page where
# renting works.
PROVIDER_ID_TO_SERVICE: dict[int, str] = {
    8: "netflix",  # Netflix
    1796: "netflix",  # Netflix Standard with Ads
    9: "amazon_prime",  # Amazon Prime Video
    10: "amazon_prime",  # Amazon Video (rent/buy)
    2100: "amazon_prime",  # Amazon Prime Video with Ads
    337: "disney_plus",  # Disney Plus
    15: "hulu",  # Hulu
    1899: "max",  # Max / HBO Max (verified live)
    384: "max",  # HBO Max (pre-rebrand legacy)
    350: "apple_tv_plus",  # Apple TV+
    2: "apple_tv_plus",  # Apple TV (rent/buy store)
    192: "youtube",  # YouTube (rent/buy, verified live)
    235: "youtube",  # YouTube Free (ads, verified live)
}

_MONETIZATION_BUCKETS = ("flatrate", "free", "ads", "rent", "buy")


@dataclass(frozen=True)
class TitleAvailability:
    """What TMDB knows about a title: where it streams, and its poster.

    streamable is None when TMDB has no opinion (no region data) — callers
    must not filter on it. The poster is independent of region data.
    """

    streamable: set[str] | None
    poster_url: str | None


def normalize_title(title: str) -> str:
    """Casefold, strip punctuation, collapse whitespace for title equality."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


class TMDBClient:
    """Async client for TMDB title resolution and watch-provider lookup."""

    def __init__(
        self,
        api_key: str,
        region: str = "US",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.region = region.upper()
        self._http_client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def get_streamable_services(self, title: str, tv_only: bool = False) -> set[str] | None:
        """Return cueso service names that can play the title, or None for "no opinion".

        None means: no plausible candidates, no region data for any candidate,
        or an API failure — callers must NOT filter on None. An empty set is a
        real answer: TMDB knows the title and no supported service streams it.
        """
        availability = await self.get_availability(title, tv_only=tv_only)
        return None if availability is None else availability.streamable

    async def get_availability(self, title: str, tv_only: bool = False) -> TitleAvailability | None:
        """Resolve a title to its streamable services and poster URL.

        None means the lookup failed outright (no plausible candidates or an
        API failure). Otherwise streamable carries the same tri-state contract
        as get_streamable_services, and poster_url is the first title-matching
        candidate's poster (None when TMDB has no artwork).
        """
        try:
            candidates = await self._search_candidates(title, tv_only)
            if not candidates:
                return None
            provider_sets = await asyncio.gather(
                *(self._region_services(media_type, tmdb_id) for media_type, tmdb_id, _ in candidates)
            )
        except (httpx.HTTPError, ValueError, KeyError) as e:
            # httpx.HTTPStatusError's str() embeds the full request URL,
            # including the api_key query param — never log it directly.
            logger.warning(
                "TMDB lookup failed for %r: %s (%s)",
                title,
                type(e).__name__,
                getattr(getattr(e, "response", None), "status_code", "n/a"),
            )
            return None
        poster_path = next((path for _, _, path in candidates if path), None)
        poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
        with_region_data = [s for s in provider_sets if s is not None]
        if not with_region_data:
            return TitleAvailability(streamable=None, poster_url=poster_url)
        streamable: set[str] = set()
        for services in with_region_data:
            streamable |= services
        return TitleAvailability(streamable=streamable, poster_url=poster_url)

    async def _search_candidates(self, title: str, tv_only: bool) -> list[tuple[str, int, str | None]]:
        """Resolve a title to (media_type, id, poster_path) triples by normalized-title equality."""
        response = await self._get_client().get(
            f"{TMDB_API_BASE}/search/multi",
            params={"api_key": self.api_key, "query": title},
            timeout=10.0,
        )
        response.raise_for_status()
        wanted = ("tv",) if tv_only else ("movie", "tv")
        target = normalize_title(title)
        candidates: list[tuple[str, int, str | None]] = []
        for result in response.json().get("results", []):
            media_type = result.get("media_type")
            if media_type not in wanted:
                continue
            name = result.get("title") or result.get("name") or ""
            if normalize_title(name) != target:
                continue
            candidates.append((media_type, result["id"], result.get("poster_path")))
            if len(candidates) == MAX_CANDIDATES:
                break
        return candidates

    async def _region_services(self, media_type: str, tmdb_id: int) -> set[str] | None:
        """Mapped services for one candidate in the region; None if region block absent."""
        response = await self._get_client().get(
            f"{TMDB_API_BASE}/{media_type}/{tmdb_id}/watch/providers",
            params={"api_key": self.api_key},
            timeout=10.0,
        )
        response.raise_for_status()
        region_block = response.json().get("results", {}).get(self.region)
        if not region_block:
            return None
        services: set[str] = set()
        for bucket in _MONETIZATION_BUCKETS:
            for provider in region_block.get(bucket, []):
                service = PROVIDER_ID_TO_SERVICE.get(provider.get("provider_id"))
                if service:
                    services.add(service)
        return services

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
