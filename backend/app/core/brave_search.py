"""Brave Search API client."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("cueso.brave_search")

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    description: str


class BraveSearchError(Exception):
    """Raised when a Brave Search API call fails."""


class BraveSearchClient:
    """Async client for the Brave Web Search API."""

    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self._http_client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def search(
        self,
        query: str,
        count: int = 10,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        """Execute a web search and return parsed results.

        Args:
            query: The search query string (may include site: filters).
            count: Maximum number of results (1-20).
            freshness: Optional freshness filter (pd, pw, pm, py).

        Returns:
            List of SearchResult objects.

        Raises:
            BraveSearchError: If the API call fails.
        """
        client = self._get_client()
        params: dict[str, Any] = {"q": query, "count": min(count, 20)}
        if freshness:
            params["freshness"] = freshness

        try:
            response = await client.get(
                BRAVE_SEARCH_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Brave Search HTTP error: %s %s",
                e.response.status_code,
                e.response.text,
            )
            raise BraveSearchError(f"Brave Search returned {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("Brave Search request failed: %s", e)
            raise BraveSearchError(f"Brave Search request failed: {e}") from e

        data = response.json()
        raw_results: list[dict[str, Any]] = data.get("web", {}).get("results", [])

        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                description=r.get("description", ""),
            )
            for r in raw_results
        ]

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
