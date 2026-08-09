# TMDB Watch-Providers Availability Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter `search_content` matches through TMDB's watch-providers data so services that cannot actually stream a title (e.g. Netflix placeholder pages) are dropped before the user sees them.

**Architecture:** A new `TMDBClient` (`app/core/tmdb.py`) resolves a title to TMDB candidates by normalized-title equality and unions their per-region providers into a set of cueso service names. `search_content` starts this oracle concurrently with the Brave search and, at the end, drops matches only when the oracle affirmatively excludes their service. Every failure path fails open (no filtering).

**Tech Stack:** Python 3.13, httpx (async), pydantic-settings, pytest + pytest-asyncio. All commands run from `backend/` with `uv run …`.

**Spec:** `docs/superpowers/specs/2026-08-08-tmdb-availability-design.md`

## Global Constraints

- Fail open everywhere: no key, timeout > 2.0 s, HTTP error, no plausible candidates, or region data absent for ALL candidates ⇒ no filtering.
- Oracle contract: `get_streamable_services(...) -> set[str] | None`; `None` = "no opinion" (never filter), empty set = real answer (drop all supported services).
- Region from config `tmdb.region`, default `"US"`. Key from `tmdb.api_key` (v3 key, `SecretStr | None`).
- Tool-result JSON shape unchanged; only the `message` string gains filter notes.
- Candidate plausibility = normalized-title equality (casefold, strip punctuation, collapse whitespace), max 3 candidates; `tv` only when season/episode present.
- All monetization buckets count as playable: `flatrate`, `free`, `ads`, `rent`, `buy`.
- Ruff (line length 120) and pyright strict must stay clean; tests via `uv run pytest`.
- Commit after each task with a focused message ending in `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: TMDBConfig

**Files:**
- Modify: `backend/app/core/config.py` (add `TMDBConfig` beside `BraveConfig` ~line 91; add field on `Settings` beside `brave` ~line 110)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `settings.tmdb.api_key: SecretStr | None`, `settings.tmdb.region: str` (default `"US"`) — consumed by Tasks 3–4.

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_config.py`)

```python
class TestTMDBConfig:
    def test_defaults(self) -> None:
        from app.core.config import TMDBConfig

        config = TMDBConfig()
        assert config.api_key is None
        assert config.region == "US"

    def test_settings_has_tmdb(self) -> None:
        from app.core.config import settings

        assert settings.tmdb is not None
        assert isinstance(settings.tmdb.region, str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -k TMDBConfig -v`
Expected: FAIL with `ImportError: cannot import name 'TMDBConfig'`

- [ ] **Step 3: Implement.** In `backend/app/core/config.py`, directly below `class BraveConfig`:

```python
class TMDBConfig(BaseModel):
    """TMDB availability-oracle settings."""

    api_key: SecretStr | None = None
    region: str = "US"
```

and on the `Settings` class, below the `brave` field:

```python
    tmdb: TMDBConfig = Field(default_factory=TMDBConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS (existing config tests must stay green)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/test_config.py
git commit -m "feat(tmdb): add TMDBConfig (api_key, region)"
```

---

### Task 2: TMDBClient oracle

**Files:**
- Create: `backend/app/core/tmdb.py`
- Test: `backend/tests/test_tmdb.py`

**Interfaces:**
- Consumes: nothing from other tasks (config wiring happens in Task 4).
- Produces (consumed by Tasks 3–4):
  - `normalize_title(title: str) -> str`
  - `PROVIDER_ID_TO_SERVICE: dict[int, str]`
  - `class TMDBClient: __init__(self, api_key: str, region: str = "US", http_client: httpx.AsyncClient | None = None)`
  - `TMDBClient.get_streamable_services(self, title: str, tv_only: bool = False) -> set[str] | None`
  - `TMDBClient.close(self) -> None` (async)

- [ ] **Step 1: Write the failing tests** as `backend/tests/test_tmdb.py`:

```python
"""Tests for the TMDB watch-providers availability oracle."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.tmdb import TMDB_API_BASE, TMDBClient, normalize_title


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _multi_result(media_type: str, tmdb_id: int, title: str) -> dict[str, Any]:
    key = "title" if media_type == "movie" else "name"
    return {"media_type": media_type, "id": tmdb_id, key: title}


def _providers_payload(region: str, bucket_to_ids: dict[str, list[int]]) -> dict[str, Any]:
    block = {
        bucket: [{"provider_id": pid, "provider_name": str(pid)} for pid in ids]
        for bucket, ids in bucket_to_ids.items()
    }
    return {"results": {region: block}}


def _client_with(routes: dict[str, MagicMock]) -> tuple[TMDBClient, AsyncMock]:
    """TMDBClient over a mock http client routing GETs by URL substring."""
    http_client = AsyncMock(spec=httpx.AsyncClient)

    async def _get(url: str, **kwargs: Any) -> MagicMock:
        for fragment, response in routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected TMDB GET: {url}")

    http_client.get.side_effect = _get
    return TMDBClient(api_key="k", http_client=http_client), http_client


class TestNormalizeTitle:
    def test_casefold_punctuation_whitespace(self) -> None:
        assert normalize_title("Bye-Bye  Birdie!") == "bye bye birdie"
        assert normalize_title("The Bear") == "the bear"

    def test_distinct_titles_stay_distinct(self) -> None:
        assert normalize_title("Masha and the Bear") != normalize_title("The Bear")


class TestGetStreamableServices:
    @pytest.mark.asyncio
    async def test_union_across_adaptations(self) -> None:
        """1963 (rent/buy on Amazon Video id 10) + 1995 (Prime id 9): both map
        to amazon_prime; netflix is absent -> not in the set."""
        client, _ = _client_with(
            {
                "/search/multi": _response(
                    {
                        "results": [
                            _multi_result("movie", 1, "Bye Bye Birdie"),
                            _multi_result("movie", 2, "Bye Bye Birdie"),
                        ]
                    }
                ),
                "/movie/1/watch/providers": _response(_providers_payload("US", {"buy": [10], "rent": [2]})),
                "/movie/2/watch/providers": _response(_providers_payload("US", {"flatrate": [9]})),
            }
        )
        services = await client.get_streamable_services("Bye Bye Birdie")
        assert services == {"amazon_prime", "apple_tv_plus"}

    @pytest.mark.asyncio
    async def test_title_collision_excluded_by_normalization(self) -> None:
        """"Masha and the Bear" (netflix) must not rescue netflix for "The Bear"."""
        client, _ = _client_with(
            {
                "/search/multi": _response(
                    {
                        "results": [
                            _multi_result("tv", 1, "The Bear"),
                            _multi_result("tv", 2, "Masha and the Bear"),
                        ]
                    }
                ),
                "/tv/1/watch/providers": _response(_providers_payload("US", {"flatrate": [15, 337]})),
                "/tv/2/watch/providers": _response(_providers_payload("US", {"flatrate": [8]})),
            }
        )
        services = await client.get_streamable_services("The Bear")
        assert services == {"hulu", "disney_plus"}

    @pytest.mark.asyncio
    async def test_tv_only_excludes_movies(self) -> None:
        client, http_client = _client_with(
            {
                "/search/multi": _response(
                    {
                        "results": [
                            _multi_result("movie", 1, "The Bear"),
                            _multi_result("tv", 2, "The Bear"),
                        ]
                    }
                ),
                "/tv/2/watch/providers": _response(_providers_payload("US", {"flatrate": [15]})),
            }
        )
        services = await client.get_streamable_services("The Bear", tv_only=True)
        assert services == {"hulu"}
        urls = [c.args[0] for c in http_client.get.call_args_list]
        assert not any("/movie/" in u for u in urls)

    @pytest.mark.asyncio
    async def test_no_candidates_returns_none(self) -> None:
        client, _ = _client_with({"/search/multi": _response({"results": []})})
        assert await client.get_streamable_services("Nonexistent") is None

    @pytest.mark.asyncio
    async def test_no_plausible_candidates_returns_none(self) -> None:
        client, _ = _client_with(
            {"/search/multi": _response({"results": [_multi_result("movie", 1, "Something Else")]})}
        )
        assert await client.get_streamable_services("The Bear") is None

    @pytest.mark.asyncio
    async def test_region_absent_for_all_candidates_returns_none(self) -> None:
        client, _ = _client_with(
            {
                "/search/multi": _response({"results": [_multi_result("movie", 1, "The Bear")]}),
                "/movie/1/watch/providers": _response({"results": {}}),
            }
        )
        assert await client.get_streamable_services("The Bear") is None

    @pytest.mark.asyncio
    async def test_region_present_but_unmapped_is_empty_set(self) -> None:
        """Region data exists but only unmapped providers -> affirmative empty set."""
        client, _ = _client_with(
            {
                "/search/multi": _response({"results": [_multi_result("movie", 1, "The Bear")]}),
                "/movie/1/watch/providers": _response(_providers_payload("US", {"flatrate": [99999]})),
            }
        )
        assert await client.get_streamable_services("The Bear") == set()

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self) -> None:
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.get.side_effect = httpx.ConnectError("boom")
        client = TMDBClient(api_key="k", http_client=http_client)
        assert await client.get_streamable_services("The Bear") is None

    @pytest.mark.asyncio
    async def test_candidate_cap_is_three(self) -> None:
        client, http_client = _client_with(
            {
                "/search/multi": _response(
                    {"results": [_multi_result("movie", i, "The Bear") for i in range(1, 6)]}
                ),
                "/watch/providers": _response(_providers_payload("US", {"flatrate": [8]})),
            }
        )
        await client.get_streamable_services("The Bear")
        provider_calls = [c for c in http_client.get.call_args_list if "/watch/providers" in c.args[0]]
        assert len(provider_calls) == 3

    @pytest.mark.asyncio
    async def test_custom_region(self) -> None:
        http_client = AsyncMock(spec=httpx.AsyncClient)

        async def _get(url: str, **kwargs: Any) -> MagicMock:
            if "/search/multi" in url:
                return _response({"results": [_multi_result("movie", 1, "The Bear")]})
            return _response(_providers_payload("GB", {"flatrate": [8]}))

        http_client.get.side_effect = _get
        client = TMDBClient(api_key="k", region="GB", http_client=http_client)
        assert await client.get_streamable_services("The Bear") == {"netflix"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tmdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.tmdb'`

- [ ] **Step 3: Implement** `backend/app/core/tmdb.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tmdb.py -v`
Expected: all PASS

- [ ] **Step 5: Lint/typecheck the new module**

Run: `uv run ruff check app/core/tmdb.py tests/test_tmdb.py && uv run ruff format app/core/tmdb.py tests/test_tmdb.py && uv run pyright app/core/tmdb.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/tmdb.py backend/tests/test_tmdb.py
git commit -m "feat(tmdb): TMDBClient availability oracle with normalized-title resolution"
```

---

### Task 3: search_content oracle integration

**Files:**
- Modify: `backend/app/core/search_and_play.py` (imports; `search_content` signature ~line 121; post-match filtering ~lines 173–225)
- Modify: `backend/tests/conftest.py` (add `mock_tmdb_client` fixture)
- Test: `backend/tests/test_search_and_play.py`

**Interfaces:**
- Consumes: `TMDBClient.get_streamable_services(title, tv_only=...) -> set[str] | None` (Task 2).
- Produces: `search_content(..., tmdb_client: TMDBClient | None = None)` — consumed by Task 4. `ORACLE_TIMEOUT_SECONDS = 2.0` module constant.

- [ ] **Step 1: Add fixture** to `backend/tests/conftest.py` (beside `mock_brave_client`):

```python
@pytest.fixture
def mock_tmdb_client() -> AsyncMock:
    from app.core.tmdb import TMDBClient

    return AsyncMock(spec=TMDBClient)
```

- [ ] **Step 2: Write the failing tests** — append to `backend/tests/test_search_and_play.py`:

```python
class TestSearchContentAvailabilityFilter:
    """TMDB oracle filtering: drop only on affirmative absence, fail open otherwise."""

    NETFLIX_RESULT = SearchResult(
        title="Watch Bye Bye Birdie | Netflix",
        url="https://www.netflix.com/title/342088",
        description="",
    )
    HULU_RESULT = SearchResult(
        title="The Bear | Hulu",
        url="https://www.hulu.com/series/the-bear-565d8976-9e52-4f30-a6f5-a47e7fe1abd4",
        description="",
    )

    @pytest.mark.asyncio
    async def test_drops_service_absent_from_oracle(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT, self.HULU_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = {"hulu"}

        result = await search_content(
            title="The Bear",
            brave_client=mock_brave_client,
            services=[NETFLIX, HULU],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["hulu"]
        assert "filtered netflix (not streamable per TMDB)" in result.message

    @pytest.mark.asyncio
    async def test_oracle_none_keeps_everything(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT, self.HULU_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = None

        result = await search_content(
            title="The Bear",
            brave_client=mock_brave_client,
            services=[NETFLIX, HULU],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert len(result.matches) == 2
        assert "filtered" not in result.message

    @pytest.mark.asyncio
    async def test_oracle_exception_fails_open(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_streamable_services.side_effect = RuntimeError("boom")

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[NETFLIX],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is True
        assert len(result.matches) == 1

    @pytest.mark.asyncio
    async def test_oracle_timeout_fails_open(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        import asyncio as _asyncio

        async def _slow(title: str, tv_only: bool = False) -> set[str]:
            await _asyncio.sleep(30)
            return set()

        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_streamable_services.side_effect = _slow

        with patch("app.core.search_and_play.ORACLE_TIMEOUT_SECONDS", 0.01):
            result = await search_content(
                title="Bye Bye Birdie",
                brave_client=mock_brave_client,
                services=[NETFLIX],
                tmdb_client=mock_tmdb_client,
            )

        assert result.success is True
        assert len(result.matches) == 1

    @pytest.mark.asyncio
    async def test_all_matches_filtered_fails_with_explanation(
        self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = set()

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[NETFLIX],
            tmdb_client=mock_tmdb_client,
        )

        assert result.success is False
        assert result.matches == []
        assert "filtered netflix (not streamable per TMDB)" in result.message

    @pytest.mark.asyncio
    async def test_tv_hint_from_season(self, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.HULU_RESULT]
        mock_tmdb_client.get_streamable_services.return_value = {"hulu"}

        await search_content(
            title="The Bear",
            season=3,
            brave_client=mock_brave_client,
            services=[HULU],
            tmdb_client=mock_tmdb_client,
        )

        mock_tmdb_client.get_streamable_services.assert_awaited_once_with("The Bear", tv_only=True)

    @pytest.mark.asyncio
    async def test_no_tmdb_client_unchanged(self, mock_brave_client: AsyncMock) -> None:
        mock_brave_client.search.return_value = [self.NETFLIX_RESULT]

        result = await search_content(
            title="Bye Bye Birdie",
            brave_client=mock_brave_client,
            services=[NETFLIX],
        )

        assert result.success is True
        assert len(result.matches) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_search_and_play.py -k AvailabilityFilter -v`
Expected: FAIL with `TypeError: search_content() got an unexpected keyword argument 'tmdb_client'`

- [ ] **Step 4: Implement** in `backend/app/core/search_and_play.py`.

Add to imports:

```python
from .tmdb import TMDBClient
```

Add module constant below `VERIFY_TIMEOUT_SECONDS`:

```python
ORACLE_TIMEOUT_SECONDS = 2.0
```

Add parameter to `search_content` (after `http_client`):

```python
    tmdb_client: TMDBClient | None = None,
```

and document it in the docstring Args:

```
        tmdb_client: Optional availability oracle; when given, matches whose
            service TMDB says cannot stream the title are dropped (fail open
            on any oracle failure).
```

At the TOP of the function body (before the Brave call), start the oracle:

```python
    oracle_task: asyncio.Task[set[str] | None] | None = None
    if tmdb_client is not None:
        oracle_task = asyncio.create_task(
            tmdb_client.get_streamable_services(title, tv_only=season is not None or episode is not None)
        )
```

In BOTH early-return branches (Brave error, no results) add before `return`:

```python
        if oracle_task is not None:
            oracle_task.cancel()
```

In the existing `if not matches:` branch (no streaming URLs matched), add the same two-line cancel before its `return`.

Replace the final success return block (`service_names = ...` through `return ContentSearchResult(...)`) with:

```python
    streamable: set[str] | None = None
    if oracle_task is not None:
        try:
            streamable = await asyncio.wait_for(oracle_task, timeout=ORACLE_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning("TMDB availability oracle failed (%s); returning matches unfiltered", e)

    filter_notes: list[str] = []
    if streamable is not None:
        kept: list[ContentMatch] = []
        for match in matches:
            if match.service_name in streamable:
                kept.append(match)
            else:
                filter_notes.append(f"filtered {match.service_name} (not streamable per TMDB)")
                logger.info(
                    "Filtered %s match %s: not streamable per TMDB", match.service_name, match.content_id
                )
        matches = kept

    if not matches:
        return ContentSearchResult(
            success=False,
            message="Found streaming URLs but none are playable: " + "; ".join(filter_notes),
            query=base_query,
            matches=[],
        )

    service_names = [m.service_name for m in matches]
    message = f"Found content on {len(matches)} service(s): {', '.join(service_names)}"
    if filter_notes:
        message += " — " + "; ".join(filter_notes)
    return ContentSearchResult(success=True, message=message, query=base_query, matches=matches)
```

(The `if not matches:` guard here can only trigger via filtering — the earlier no-URL-match branch already returned — so `filter_notes` is never empty in that message.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_search_and_play.py -v`
Expected: all PASS (including the pre-existing verification and dedup tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/search_and_play.py backend/tests/test_search_and_play.py backend/tests/conftest.py
git commit -m "feat(tmdb): filter search_content matches through the availability oracle"
```

---

### Task 4: Wiring, config example, attribution

**Files:**
- Modify: `backend/app/core/llm/tool_executor.py` (`RokuECPToolExecutor.__init__` ~line 168; `_find_content` ~line 237)
- Modify: `backend/app/api/chat.py` (`get_tool_executor`, roku_ecp branch ~lines 84–91)
- Modify: `backend/config.yml.example`
- Modify: `README.md` (repo root)
- Test: `backend/tests/test_tool_executor.py`

**Interfaces:**
- Consumes: `TMDBClient` (Task 2), `search_content(..., tmdb_client=...)` (Task 3), `settings.tmdb` (Task 1).
- Produces: `RokuECPToolExecutor(roku_ip, http_client, brave_client=None, tmdb_client: TMDBClient | None = None)`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_tool_executor.py`, mirroring that file's existing `_find_content` test setup (same fixtures/construction it already uses for the brave_client cases):

```python
class TestFindContentTMDBWiring:
    @pytest.mark.asyncio
    async def test_find_content_passes_tmdb_client(
        self, mock_http_client: AsyncMock, mock_brave_client: AsyncMock, mock_tmdb_client: AsyncMock
    ) -> None:
        from app.core.search_and_play import ContentSearchResult

        executor = RokuECPToolExecutor(
            "192.168.1.100", mock_http_client, mock_brave_client, tmdb_client=mock_tmdb_client
        )
        with patch(
            "app.core.llm.tool_executor.search_content",
            new_callable=AsyncMock,
            return_value=ContentSearchResult(success=True, message="", query="q", matches=[]),
        ) as mock_search:
            await executor.execute_tool(make_tool_call("find_content", {"title": "The Bear"}))

        assert mock_search.call_args.kwargs["tmdb_client"] is mock_tmdb_client
```

Adapt `make_tool_call` / executor construction to the helpers that file actually uses (read it first; it already tests `_find_content` via `execute_tool`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_executor.py -k TMDBWiring -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'tmdb_client'`

- [ ] **Step 3: Implement.** In `tool_executor.py`: add `TMDBClient` to the `TYPE_CHECKING` import block (beside `BraveSearchClient`):

```python
    from ..tmdb import TMDBClient
```

extend `RokuECPToolExecutor.__init__`:

```python
    def __init__(
        self,
        roku_ip: str,
        http_client: Any,
        brave_client: BraveSearchClient | None = None,
        tmdb_client: TMDBClient | None = None,
    ) -> None:
```

with `self.tmdb_client = tmdb_client` beside `self.brave_client = brave_client`, and in `_find_content` add `tmdb_client=self.tmdb_client,` after `http_client=self.http_client,`.

In `chat.py`'s roku_ecp branch, after the brave_client construction:

```python
        tmdb_client = None
        if settings.tmdb.api_key:
            tmdb_client = TMDBClient(
                api_key=settings.tmdb.api_key.get_secret_value(),
                region=settings.tmdb.region,
                http_client=http_client,
            )
        return RokuECPToolExecutor(settings.roku.ip, http_client, brave_client, tmdb_client)
```

with `from ..core.tmdb import TMDBClient` added beside the existing `BraveSearchClient` import.

In `backend/config.yml.example`, after the `brave:` block:

```yaml
# Optional: TMDB availability filtering (free API key from themoviedb.org).
# Drops search matches for services that cannot actually stream the title.
tmdb:
  api_key: your_tmdb_v3_api_key
  region: US
```

In `README.md`, add to the credits/acknowledgments area (or create a one-line section at the bottom):

```markdown
Streaming availability data powered by [TMDB](https://www.themoviedb.org) and [JustWatch](https://www.justwatch.com).
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/llm/tool_executor.py backend/app/api/chat.py backend/config.yml.example README.md backend/tests/test_tool_executor.py
git commit -m "feat(tmdb): wire availability oracle into executor and chat setup"
```

---

### Task 5: Smoke script + final verification

**Files:**
- Create: `backend/scripts/tmdb_smoke.py`

**Interfaces:**
- Consumes: `TMDBClient` (Task 2), `search_content` (Task 3), `settings` (Task 1).

- [ ] **Step 1: Write the smoke script** `backend/scripts/tmdb_smoke.py` (manual, live-API; not collected by pytest):

```python
"""Live TMDB smoke test: oracle timing + end-to-end filtered search.

Run from backend/ (needs config.yml with brave + tmdb keys):
    uv run python scripts/tmdb_smoke.py
"""

import asyncio
import time

import httpx

from app.core.brave_search import BraveSearchClient
from app.core.config import settings
from app.core.search_and_play import search_content
from app.core.tmdb import TMDBClient


async def main() -> None:
    assert settings.tmdb.api_key is not None, "tmdb.api_key missing from config.yml"
    assert settings.brave.api_key is not None, "brave.api_key missing from config.yml"

    async with httpx.AsyncClient() as http_client:
        tmdb = TMDBClient(
            api_key=settings.tmdb.api_key.get_secret_value(),
            region=settings.tmdb.region,
            http_client=http_client,
        )

        for title in ["Bye Bye Birdie", "The Bear", "Moana 2"]:
            t0 = time.perf_counter()
            services = await tmdb.get_streamable_services(title)
            print(f"oracle {title!r}: {(time.perf_counter() - t0) * 1000:.0f}ms -> {services}")

        brave = BraveSearchClient(api_key=settings.brave.api_key.get_secret_value(), http_client=http_client)
        t0 = time.perf_counter()
        result = await search_content(
            "Bye Bye Birdie", brave_client=brave, http_client=http_client, tmdb_client=tmdb
        )
        print(f"\nsearch_content('Bye Bye Birdie'): {(time.perf_counter() - t0) * 1000:.0f}ms")
        print(f"  message: {result.message}")
        for m in result.matches:
            print(f"  {m.service_name}: {m.content_id}")
        assert all(m.service_name != "netflix" for m in result.matches), "Netflix placeholder not filtered!"


asyncio.run(main())
```

- [ ] **Step 2: Run the smoke script live**

Run: `uv run python scripts/tmdb_smoke.py`
Expected: oracle lines ≤ ~500 ms each; final search shows the Netflix placeholder filtered (message contains `filtered netflix (not streamable per TMDB)`) and amazon_prime present with content_id `B001G5RFHE`.

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright .`
Expected: all pass, no reformat needed, 0 pyright errors

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/tmdb_smoke.py
git commit -m "feat(tmdb): live smoke script for oracle timing and filtered search"
```
