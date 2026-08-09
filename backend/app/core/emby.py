"""Emby media server client for local library search."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("cueso.emby")

# Roku channel ID for the Emby app, per roku-deeplink spec (launch-only channel).
EMBY_CHANNEL_ID = 44191


@dataclass
class EmbyItem:
    """A playable item from the Emby library."""

    item_id: str
    name: str
    item_type: str  # "Movie" | "Series" | "Episode"
    resume_position_ticks: int | None = None  # None when unwatched or fully watched


class EmbyError(Exception):
    """Raised when an Emby API call fails."""


class EmbyClient:
    """Async client for a self-hosted Emby server's REST API."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        user_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self._http_client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        try:
            response = await client.get(
                f"{self.server_url}{path}",
                params=params,
                headers={"X-Emby-Token": self.api_key},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error("Emby HTTP error: %s %s", e.response.status_code, e.response.text)
            raise EmbyError(f"Emby returned {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("Emby request failed: %s", e)
            raise EmbyError(f"Emby request failed: {e}") from e
        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            logger.error("Emby returned invalid JSON: %s", e)
            raise EmbyError(f"Emby returned invalid JSON: {e}") from e
        return data

    async def search(self, title: str, season: int | None = None, episode: int | None = None) -> list[EmbyItem]:
        """Search the library for movies and series matching a title.

        When season and episode are given, series matches are resolved to the
        concrete episode (dropped if that episode doesn't exist); movie matches
        pass through unchanged. Uses the user-scoped endpoint so resume
        positions (UserData.PlaybackPositionTicks) ride along.
        """
        data = await self._get(
            f"/emby/Users/{self.user_id}/Items",
            {"SearchTerm": title, "IncludeItemTypes": "Movie,Series", "Recursive": "true"},
        )
        raw_items: list[dict[str, Any]] = data.get("Items", [])
        items = [self._parse_item(raw) for raw in raw_items]

        if season is None or episode is None:
            return items

        resolved: list[EmbyItem] = []
        for item in items:
            if item.item_type == "Series":
                found = await self._find_episode(item.item_id, season, episode)
                if found is not None:
                    resolved.append(found)
            else:
                resolved.append(item)
        return resolved

    async def _find_episode(self, series_id: str, season: int, episode: int) -> EmbyItem | None:
        data = await self._get(
            f"/emby/Shows/{series_id}/Episodes",
            {"Season": season, "UserId": self.user_id},
        )
        raw_items: list[dict[str, Any]] = data.get("Items", [])
        for raw in raw_items:
            if raw.get("IndexNumber") == episode:
                return self._parse_item(raw)
        return None

    @staticmethod
    def _parse_item(raw: dict[str, Any]) -> EmbyItem:
        user_data: dict[str, Any] = raw.get("UserData") or {}
        ticks = user_data.get("PlaybackPositionTicks", 0)
        return EmbyItem(
            item_id=str(raw.get("Id", "")),
            name=str(raw.get("Name", "")),
            item_type=str(raw.get("Type", "")),
            resume_position_ticks=ticks if ticks else None,
        )

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
