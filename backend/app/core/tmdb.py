"""TMDB watch-providers availability oracle.

Answers "which cueso services can actually play this title?" using TMDB's
JustWatch-licensed watch-providers data. Used by search_content to drop
matches for services that cannot stream a title (e.g. Netflix placeholder
pages). Design: docs/superpowers/specs/2026-08-08-tmdb-availability-design.md
"""

import asyncio
import logging
import re

import httpx

logger = logging.getLogger("cueso.tmdb")

TMDB_API_BASE = "https://api.themoviedb.org/3"
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
}

_MONETIZATION_BUCKETS = ("flatrate", "free", "ads", "rent", "buy")


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
        self.region = region
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
        try:
            candidates = await self._search_candidates(title, tv_only)
            if not candidates:
                return None
            provider_sets = await asyncio.gather(
                *(self._region_services(media_type, tmdb_id) for media_type, tmdb_id in candidates)
            )
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning("TMDB lookup failed for %r: %s", title, e)
            return None
        with_region_data = [s for s in provider_sets if s is not None]
        if not with_region_data:
            return None
        streamable: set[str] = set()
        for services in with_region_data:
            streamable |= services
        return streamable

    async def _search_candidates(self, title: str, tv_only: bool) -> list[tuple[str, int]]:
        """Resolve a title to (media_type, id) pairs by normalized-title equality."""
        response = await self._get_client().get(
            f"{TMDB_API_BASE}/search/multi",
            params={"api_key": self.api_key, "query": title},
            timeout=10.0,
        )
        response.raise_for_status()
        wanted = ("tv",) if tv_only else ("movie", "tv")
        target = normalize_title(title)
        candidates: list[tuple[str, int]] = []
        for result in response.json().get("results", []):
            media_type = result.get("media_type")
            if media_type not in wanted:
                continue
            name = result.get("title") or result.get("name") or ""
            if normalize_title(name) != target:
                continue
            candidates.append((media_type, result["id"]))
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
