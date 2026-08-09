# Emby Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the user's self-hosted Emby server searchable through `find_content` and playable on Roku via the Emby channel (44191), including resume — per the approved spec at `docs/superpowers/specs/2026-08-08-emby-integration-design.md`.

**Architecture:** New `app/core/emby.py` client module (mirrors `brave_search.py`); `search_content()` merges Emby + Brave results concurrently, Emby first; `launch_on_roku()` gains the roku-deeplink spec's launch-only Emby branch (`Command=PlayNow&ItemIds={id}`, optional `StartPositionTicks`, no keypress). Tool layer and system prompt wire it to the LLM.

**Tech Stack:** Python 3.13, FastAPI, httpx (async), Pydantic Settings, pytest + pytest-asyncio, uv, speclib.

## Global Constraints

- All commands run from `backend/` with `uv run …`.
- Pyright **strict** mode must stay clean: annotate everything, including test helpers.
- Ruff rules E,F,I,N,W,B,C4,UP,RUF; line length 120.
- Emby's Roku channel ID is `44191` (roku-deeplink spec); launch params for Emby are `Command=PlayNow&ItemIds={id}` with optional `&StartPositionTicks={ticks}` — **param order matters** for the spec fixtures, and dict insertion order is what serializes.
- Emby channels are launch-only: no wait, no keypress — ever.
- Never edit `speclib.toml` / `speclib.lock` by hand; only via `speclib sync --record`.
- The provenance headers in `app/core/streaming.py` and `app/core/search_and_play.py` stay at `roku-deeplink v1.5.0` (the spec version is unchanged by this work; the repo synced to v1.5.0 on 2026-08-08 — do not touch the v1.5.0 verification-probe or query-shape code).
- Work on the `emby-integration` branch. Commit after each task, message trailer:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Emby config section

**Files:**
- Modify: `backend/app/core/config.py` (add `EmbyConfig` after `BraveConfig`, ~line 95; add `emby` field to `Settings`, ~line 110)
- Modify: `config.yml.example` (repo root; add `emby:` section after `brave:`)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `settings.emby: EmbyConfig` with `server_url: str` (default `""`), `api_key: SecretStr | None` (default `None`), `user_id: str` (default `""`). Emby is considered configured iff `settings.emby.server_url` is non-empty AND `settings.emby.api_key` is set (Task 6 relies on this rule).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config.py`:

```python
class TestEmbyConfig:
    def test_defaults(self) -> None:
        from app.core.config import EmbyConfig

        config = EmbyConfig()
        assert config.server_url == ""
        assert config.api_key is None
        assert config.user_id == ""

    def test_settings_have_emby_section(self) -> None:
        from app.core.config import EmbyConfig, Settings

        settings = Settings()
        assert isinstance(settings.emby, EmbyConfig)
```

Note: match the existing import style in `test_config.py` — if it imports at module top, put the imports there instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v -k Emby`
Expected: FAIL with `ImportError: cannot import name 'EmbyConfig'`

- [ ] **Step 3: Implement**

In `backend/app/core/config.py`, after the `BraveConfig` class:

```python
class EmbyConfig(BaseModel):
    """Emby self-hosted media server settings."""

    server_url: str = ""
    api_key: SecretStr | None = None
    user_id: str = ""
```

In `Settings`, after the `brave` field:

```python
    emby: EmbyConfig = Field(default_factory=EmbyConfig)
```

In `config.yml.example` (repo root), after the `brave:` block:

```yaml
# Emby self-hosted media server (optional).
# Leave server_url empty to disable Emby library search.
# user_id: open the Emby dashboard -> Users -> select your user; the id is in
# the URL. Or GET {server_url}/emby/Users with your api key.
emby:
  server_url: ""              # e.g. "http://192.168.1.50:8096"
  api_key: ""
  user_id: ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright .
git add app/core/config.py tests/test_config.py ../config.yml.example
git commit -m "feat(emby): add emby config section

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: EmbyClient module

**Files:**
- Create: `backend/app/core/emby.py`
- Test (create): `backend/tests/test_emby.py`

**Interfaces:**
- Consumes: nothing from other tasks (client takes explicit constructor args, not `settings`).
- Produces (Tasks 4–6 rely on these exact names):
  - `EMBY_CHANNEL_ID: int = 44191`
  - `@dataclass EmbyItem`: `item_id: str`, `name: str`, `item_type: str` (`"Movie" | "Series" | "Episode"`), `resume_position_ticks: int | None = None`
  - `class EmbyError(Exception)`
  - `class EmbyClient`: `__init__(server_url: str, api_key: str, user_id: str, http_client: httpx.AsyncClient | None = None)`; attribute `server_url` (trailing slash stripped); `async search(title: str, season: int | None = None, episode: int | None = None) -> list[EmbyItem]`; `async close() -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_emby.py`:

```python
"""Tests for the Emby media server client."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.emby import EMBY_CHANNEL_ID, EmbyClient, EmbyError


def _response(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _movie(item_id: str = "m1", name: str = "Heat", ticks: int = 0) -> dict[str, Any]:
    return {"Id": item_id, "Name": name, "Type": "Movie", "UserData": {"PlaybackPositionTicks": ticks}}


def _series(item_id: str = "s1", name: str = "Severance") -> dict[str, Any]:
    return {"Id": item_id, "Name": name, "Type": "Series", "UserData": {"PlaybackPositionTicks": 0}}


@pytest.fixture
def http_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def client(http_client: AsyncMock) -> EmbyClient:
    return EmbyClient("http://emby.local:8096/", "key123", "user1", http_client=http_client)


def test_channel_id_matches_spec() -> None:
    assert EMBY_CHANNEL_ID == 44191


def test_server_url_trailing_slash_stripped(client: EmbyClient) -> None:
    assert client.server_url == "http://emby.local:8096"


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_request_shape(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": []})

        await client.search("Heat")

        args, kwargs = http_client.get.call_args
        assert args[0] == "http://emby.local:8096/emby/Users/user1/Items"
        assert kwargs["headers"] == {"X-Emby-Token": "key123"}
        assert kwargs["params"]["SearchTerm"] == "Heat"
        assert kwargs["params"]["IncludeItemTypes"] == "Movie,Series"
        assert kwargs["params"]["Recursive"] == "true"

    @pytest.mark.asyncio
    async def test_parses_items(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie(ticks=12000000000)]})

        items = await client.search("Heat")

        assert len(items) == 1
        assert items[0].item_id == "m1"
        assert items[0].name == "Heat"
        assert items[0].item_type == "Movie"
        assert items[0].resume_position_ticks == 12000000000

    @pytest.mark.asyncio
    async def test_zero_ticks_means_no_resume(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie(ticks=0)]})

        items = await client.search("Heat")

        assert items[0].resume_position_ticks is None

    @pytest.mark.asyncio
    async def test_season_episode_resolves_episode(self, client: EmbyClient, http_client: AsyncMock) -> None:
        episode = {
            "Id": "ep23",
            "Name": "The We We Are",
            "Type": "Episode",
            "IndexNumber": 3,
            "UserData": {"PlaybackPositionTicks": 500},
        }
        http_client.get.side_effect = [
            _response({"Items": [_series()]}),
            _response({"Items": [{"Id": "ep21", "IndexNumber": 1, "Type": "Episode", "Name": "e1"}, episode]}),
        ]

        items = await client.search("Severance", season=2, episode=3)

        assert len(items) == 1
        assert items[0].item_id == "ep23"
        assert items[0].item_type == "Episode"
        assert items[0].resume_position_ticks == 500
        args, kwargs = http_client.get.call_args
        assert args[0] == "http://emby.local:8096/emby/Shows/s1/Episodes"
        assert kwargs["params"]["Season"] == 2
        assert kwargs["params"]["UserId"] == "user1"

    @pytest.mark.asyncio
    async def test_episode_not_found_drops_series(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.side_effect = [
            _response({"Items": [_series()]}),
            _response({"Items": [{"Id": "ep21", "IndexNumber": 1, "Type": "Episode", "Name": "e1"}]}),
        ]

        items = await client.search("Severance", season=2, episode=99)

        assert items == []

    @pytest.mark.asyncio
    async def test_movie_kept_when_season_episode_given(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.return_value = _response({"Items": [_movie()]})

        items = await client.search("Heat", season=2, episode=3)

        assert len(items) == 1
        assert items[0].item_type == "Movie"
        assert http_client.get.call_count == 1  # no episode lookup for movies

    @pytest.mark.asyncio
    async def test_http_status_error_raises_emby_error(self, client: EmbyClient, http_client: AsyncMock) -> None:
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401, text="Unauthorized")
        )
        http_client.get.return_value = resp

        with pytest.raises(EmbyError):
            await client.search("Heat")

    @pytest.mark.asyncio
    async def test_request_error_raises_emby_error(self, client: EmbyClient, http_client: AsyncMock) -> None:
        http_client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(EmbyError):
            await client.search("Heat")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_emby.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.emby'`

- [ ] **Step 3: Implement `backend/app/core/emby.py`**

```python
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
        data: dict[str, Any] = response.json()
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
        ticks = raw.get("UserData", {}).get("PlaybackPositionTicks", 0)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_emby.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright .
git add app/core/emby.py tests/test_emby.py
git commit -m "feat(emby): add EmbyClient for library search

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: launch-only Emby branch in `launch_on_roku` (+ `ContentMatch` fields)

**Files:**
- Modify: `backend/app/core/search_and_play.py` (`ContentMatch` ~line 20; `launch_on_roku` ~line 167)
- Test: `backend/tests/test_search_and_play.py`

**Interfaces:**
- Consumes: `EMBY_CHANNEL_ID` from `app.core.emby` (Task 2).
- Produces (Tasks 4–6 rely on these):
  - `ContentMatch.post_launch_key: str | None = "Select"` (None = launch-only) and new field `resume_position_ticks: int | None = None`.
  - `launch_on_roku(channel_id: int, content_id: str, roku_base_url: str, http_client: httpx.AsyncClient, media_type: str = "movie", post_launch_key: str | None = "Select", resume_position_ticks: int | None = None) -> LaunchResult`.
  - Behavior: channel 44191 → params `{"Command": "PlayNow", "ItemIds": content_id}` (+ `"StartPositionTicks": str(ticks)` when given), key forced to None; `post_launch_key is None` → return success right after the launch POST (no sleep, no keypress).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_search_and_play.py` (reuse the file's existing imports of `launch_on_roku`, `AsyncMock`, `MagicMock`, `patch`, `pytest`):

```python
class TestLaunchOnRokuEmby:
    """Emby (channel 44191) is launch-only per roku-deeplink spec."""

    @pytest.mark.asyncio
    async def test_emby_launch_only(self, mock_http_client: AsyncMock) -> None:
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await launch_on_roku(
                channel_id=44191,
                content_id="3f9a1c",
                roku_base_url="http://192.168.1.100:8060",
                http_client=mock_http_client,
            )

        assert result.success is True
        assert mock_http_client.post.call_count == 1
        args, kwargs = mock_http_client.post.call_args
        assert args[0] == "http://192.168.1.100:8060/launch/44191"
        assert kwargs["params"] == {"Command": "PlayNow", "ItemIds": "3f9a1c"}
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emby_resume_position(self, mock_http_client: AsyncMock) -> None:
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        await launch_on_roku(
            channel_id=44191,
            content_id="3f9a1c",
            roku_base_url="http://192.168.1.100:8060",
            http_client=mock_http_client,
            resume_position_ticks=12000000000,
        )

        _, kwargs = mock_http_client.post.call_args
        assert kwargs["params"] == {
            "Command": "PlayNow",
            "ItemIds": "3f9a1c",
            "StartPositionTicks": "12000000000",
        }

    @pytest.mark.asyncio
    async def test_emby_ignores_post_launch_key(self, mock_http_client: AsyncMock) -> None:
        """Even if a caller passes a key, Emby never gets a keypress."""
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        await launch_on_roku(
            channel_id=44191,
            content_id="3f9a1c",
            roku_base_url="http://192.168.1.100:8060",
            http_client=mock_http_client,
            post_launch_key="Select",
        )

        assert mock_http_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_none_key_is_launch_only_for_any_channel(self, mock_http_client: AsyncMock) -> None:
        """Spec Function 2: a descriptor with no post_launch_key is launch-only."""
        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await launch_on_roku(
                channel_id=12,
                content_id="81444554",
                roku_base_url="http://192.168.1.100:8060",
                http_client=mock_http_client,
                post_launch_key=None,
            )

        assert result.success is True
        assert mock_http_client.post.call_count == 1
        _, kwargs = mock_http_client.post.call_args
        assert kwargs["params"] == {"contentId": "81444554", "mediaType": "movie"}
        mock_sleep.assert_not_awaited()


class TestContentMatchFields:
    def test_launch_only_match(self) -> None:
        match = ContentMatch(
            service_name="emby",
            channel_id=44191,
            content_id="3f9a1c",
            source_url="http://emby.local:8096/web/index.html#!/item?id=3f9a1c",
            title="Heat",
            media_type="movie",
            post_launch_key=None,
            resume_position_ticks=12000000000,
        )
        assert match.post_launch_key is None
        assert match.resume_position_ticks == 12000000000

    def test_resume_defaults_to_none(self) -> None:
        match = ContentMatch(
            service_name="netflix",
            channel_id=12,
            content_id="81444554",
            source_url="https://netflix.com/watch/81444554",
            title="Heat",
            media_type="movie",
        )
        assert match.post_launch_key == "Select"
        assert match.resume_position_ticks is None
```

Add `ContentMatch` to the file's imports from `app.core.search_and_play` if not already there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search_and_play.py -v -k "Emby or ContentMatchFields"`
Expected: FAIL — `resume_position_ticks` unexpected keyword; Emby launch asserts wrong params and 2 POST calls.

- [ ] **Step 3: Implement in `backend/app/core/search_and_play.py`**

Add to the imports:

```python
from .emby import EMBY_CHANNEL_ID
```

Change `ContentMatch`'s last field and add one:

```python
    post_launch_key: str | None = "Select"  # Key to press after launch; None = launch-only (Emby)
    resume_position_ticks: int | None = None  # Emby resume position, when partially watched
```

Replace `launch_on_roku`'s signature, docstring intro, and the params/keypress logic (keep the existing HTTP error handling identical):

```python
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
    """
    # Emby is launch-only per roku-deeplink spec, whatever the caller passed.
    if channel_id == EMBY_CHANNEL_ID:
        params: dict[str, str] = {"Command": "PlayNow", "ItemIds": content_id}
        if resume_position_ticks is not None:
            params["StartPositionTicks"] = str(resume_position_ticks)
        post_launch_key = None
    else:
        params = {"contentId": content_id, "mediaType": media_type}
```

After the launch POST succeeds (status 200), insert before the wait step:

```python
    if post_launch_key is None:
        return LaunchResult(
            success=True,
            message=f"Launched channel {channel_id} with content ID {content_id} (launch-only).",
            status_code=200,
        )
```

Keep the wait + keypress code after that unchanged (it now only runs when a key is set). Keep the existing `httpx` import and the type annotation on `http_client` (it was `httpx.AsyncClient` already via the module's imports — do not change other callers).

- [ ] **Step 4: Run the full search_and_play tests**

Run: `uv run pytest tests/test_search_and_play.py tests/test_roku_deeplink_fixtures.py -v`
Expected: all PASS (existing tests unaffected: default `post_launch_key="Select"` preserved)

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright .
git add app/core/search_and_play.py tests/test_search_and_play.py
git commit -m "feat(emby): launch-only Emby branch in launch_on_roku

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Run the Emby playback fixtures

**Files:**
- Modify: `backend/tests/test_roku_deeplink_fixtures.py`

**Interfaces:**
- Consumes: Task 3's `launch_on_roku` (launch-only when `post_launch_key` is None, Emby params, `resume_position_ticks`).
- Produces: all 8 materialized playback fixtures run, Emby included (Task 7 records the sync; status stays `skip` because YouTube fixtures are excluded at materialization).

- [ ] **Step 1: Update the fixture test**

In `backend/tests/test_roku_deeplink_fixtures.py`:

1. Module docstring: the final paragraph covers both Emby and YouTube. Replace only its Emby sentences (everything up to and including "matching in the first place).") with the text below; keep the YouTube sentences that follow unchanged:

```
Emby (channel_id 44191) is launch-only and addressed by descriptor, not URL:
its 2 playback fixtures run against ``launch_on_roku`` (single launch POST,
PlayNow params, optional StartPositionTicks, no wait/keypress); it is never
produced by URL matching.
```

2. Delete the `EMBY_CHANNEL_ID = "44191"` constant, the `_PLAYBACK_CASES`/`_EXCLUDED_PLAYBACK` split, and the whole `test_emby_playback_fixtures_are_excluded` function.

3. Replace `TestSpecFunction2PlaybackCommands` with:

```python
_PLAYBACK_CASES: list[dict[str, Any]] = FIXTURES["playback_commands"]


class TestSpecFunction2PlaybackCommands:
    """Function 2 (live: search_and_play.launch_on_roku) emits the spec's action
    sequence for every playback fixture: launch -> wait 2000ms -> keypress for
    URL channels, a single launch for Emby (launch-only)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case",
        _PLAYBACK_CASES,
        ids=[f"{c['input']['channel_name']}-{i}" for i, c in enumerate(_PLAYBACK_CASES)],
    )
    async def test_playback_sequence(self, case: dict[str, Any], mock_http_client: AsyncMock) -> None:
        descriptor = case["input"]
        expected_actions = case["expected"]["actions"]

        ok = MagicMock()
        ok.status_code = 200
        mock_http_client.post.return_value = ok

        with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await launch_on_roku(
                channel_id=int(descriptor["channel_id"]),
                content_id=descriptor["content_id"],
                roku_base_url=ROKU_BASE_URL,
                http_client=mock_http_client,
                media_type=descriptor.get("media_type", "movie"),
                post_launch_key=descriptor.get("post_launch_key"),
                resume_position_ticks=descriptor.get("resume_position_ticks"),
            )

        assert result.success is True
        assert case["expected"]["type"] == "action_sequence"
        calls = mock_http_client.post.call_args_list

        # Action 1 is always the launch, with exact spec params (order matters).
        launch_action = expected_actions[0]
        assert launch_action["type"] == "launch"
        assert f"/launch/{launch_action['channel_id']}" in calls[0].args[0]
        params = calls[0].kwargs["params"]
        serialized = "&".join(f"{k}={v}" for k, v in params.items())
        assert serialized == launch_action["params"]

        if len(expected_actions) == 1:
            # Launch-only channel (Emby): no wait, no keypress.
            assert len(calls) == 1
            mock_sleep.assert_not_awaited()
        else:
            assert [a["type"] for a in expected_actions] == ["launch", "wait", "keypress"]
            _, wait_action, keypress_action = expected_actions
            assert len(calls) == 2
            mock_sleep.assert_awaited_once_with(wait_action["milliseconds"] / 1000)
            assert f"/keypress/{keypress_action['key']}" in calls[1].args[0]
            assert keypress_action["count"] == 1
```

Note: the spec fixture inputs for URL channels carry `media_type` and `post_launch_key`; Emby inputs carry neither (and optionally `resume_position_ticks`) — `descriptor.get(...)` encodes exactly the spec's "descriptor may lack a post-launch key" semantics.

- [ ] **Step 2: Run the fixture tests**

Run: `uv run pytest tests/test_roku_deeplink_fixtures.py -v`
Expected: all PASS, and the output shows 8 playback cases including two `Emby-*` ids (one with `StartPositionTicks`)

- [ ] **Step 3: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright .
git add tests/test_roku_deeplink_fixtures.py
git commit -m "test(emby): run the 2 Emby playback fixtures against live launch path

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Merge Emby into `search_content`

**Files:**
- Modify: `backend/app/core/search_and_play.py` (`search_content` ~line 73)
- Modify: `backend/tests/conftest.py` (add `mock_emby_client` fixture)
- Test: `backend/tests/test_search_and_play.py`

**Interfaces:**
- Consumes: `EmbyClient`, `EmbyError`, `EmbyItem` (Task 2); `ContentMatch` fields (Task 3).
- Produces (Task 6 relies on this):
  - `search_content(title: str, brave_client: BraveSearchClient | None, season=None, episode=None, episode_title=None, media_type=None, services=None, http_client: httpx.AsyncClient | None = None, emby_client: EmbyClient | None = None) -> ContentSearchResult` — `brave_client` may now be None (streaming search skipped with failure message "Brave Search is not configured."); `http_client` is the existing v1.5.0 probe param, unchanged.
  - Emby matches come first in `result.matches`, with `service_name="emby"`, `channel_id=44191`, `post_launch_key=None`.
  - Emby failure (EmbyError) degrades to streaming-only; both-empty → `success=False`.

- [ ] **Step 1: Add the conftest fixture**

Append to `backend/tests/conftest.py` (add `from app.core.emby import EmbyClient` to its imports):

```python
@pytest.fixture
def mock_emby_client() -> AsyncMock:
    client = AsyncMock(spec=EmbyClient)
    client.server_url = "http://emby.local:8096"
    return client
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_search_and_play.py` (add `EmbyError`, `EmbyItem` to imports from `app.core.emby`; `SearchResult` from `app.core.brave_search` is already imported there):

```python
class TestSearchContentEmby:
    @pytest.mark.asyncio
    async def test_emby_matches_come_first(
        self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock
    ) -> None:
        mock_emby_client.search.return_value = [
            EmbyItem(item_id="m1", name="Heat", item_type="Movie", resume_position_ticks=42)
        ]
        mock_brave_client.search.return_value = [
            SearchResult(title="Heat | Netflix", url="https://www.netflix.com/watch/81444554", description="")
        ]

        result = await search_content("Heat", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["emby", "netflix"]
        emby = result.matches[0]
        assert emby.channel_id == 44191
        assert emby.content_id == "m1"
        assert emby.title == "Heat"
        assert emby.media_type == "movie"
        assert emby.post_launch_key is None
        assert emby.resume_position_ticks == 42
        assert emby.source_url == "http://emby.local:8096/web/index.html#!/item?id=m1"

    @pytest.mark.asyncio
    async def test_series_media_type(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = [EmbyItem(item_id="s1", name="Severance", item_type="Series")]
        mock_brave_client.search.return_value = []

        result = await search_content("Severance", mock_brave_client, emby_client=mock_emby_client)

        assert result.matches[0].media_type == "series"

    @pytest.mark.asyncio
    async def test_emby_failure_degrades_to_streaming(
        self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock
    ) -> None:
        mock_emby_client.search.side_effect = EmbyError("server down")
        mock_brave_client.search.return_value = [
            SearchResult(title="Heat | Netflix", url="https://www.netflix.com/watch/81444554", description="")
        ]

        result = await search_content("Heat", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["netflix"]

    @pytest.mark.asyncio
    async def test_emby_passes_season_episode(
        self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock
    ) -> None:
        mock_emby_client.search.return_value = []
        mock_brave_client.search.return_value = []

        await search_content("Severance", mock_brave_client, season=2, episode=3, emby_client=mock_emby_client)

        mock_emby_client.search.assert_awaited_once_with("Severance", season=2, episode=3)

    @pytest.mark.asyncio
    async def test_emby_only_no_brave(self, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = [EmbyItem(item_id="m1", name="Heat", item_type="Movie")]

        result = await search_content("Heat", None, emby_client=mock_emby_client)

        assert result.success is True
        assert [m.service_name for m in result.matches] == ["emby"]

    @pytest.mark.asyncio
    async def test_nothing_found_anywhere(self, mock_brave_client: AsyncMock, mock_emby_client: AsyncMock) -> None:
        mock_emby_client.search.return_value = []
        mock_brave_client.search.return_value = []

        result = await search_content("Nonexistent", mock_brave_client, emby_client=mock_emby_client)

        assert result.success is False
        assert result.matches == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_search_and_play.py -v -k Emby`
Expected: FAIL with `TypeError: search_content() got an unexpected keyword argument 'emby_client'`

- [ ] **Step 4: Implement in `backend/app/core/search_and_play.py`**

Update the emby import (Task 3 added `EMBY_CHANNEL_ID`):

```python
from .emby import EMBY_CHANNEL_ID, EmbyClient, EmbyError
```

Add near the other module constants:

```python
_EMBY_MEDIA_TYPES = {"Movie": "movie", "Series": "series", "Episode": "episode"}
```

Replace the body of `search_content` with a concurrent two-source search. The existing Brave/URL-matching logic — **including the v1.5.0 verification-probe block** (the `_verify_match` call and its "Rejected … candidate" logging; `_verify_match` itself stays module-level and unchanged) — moves verbatim into `_search_streaming` (same log lines, same messages). The new `_search_emby` wraps the Emby client. Note `search_content` keeps its v1.5.0 `http_client` probe parameter; `emby_client` goes after it:

```python
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
) -> ContentSearchResult:
    """Search the local Emby library and streaming services concurrently.

    Emby matches come first (the user's own server). An Emby failure degrades
    to streaming-only results; a missing Brave client degrades to Emby-only.

    Args:
        http_client: Client for verification probes (spec §11); omitting it
            skips probes (fail open).
        emby_client: Client for the local Emby library; omitting it skips
            Emby search.

    Returns:
        ContentSearchResult with all matches across sources.
    """
    base_query = build_search_query(title, season, episode, episode_title)

    emby_matches, streaming = await asyncio.gather(
        _search_emby(emby_client, title, season, episode, media_type),
        _search_streaming(brave_client, base_query, media_type, services, http_client),
    )
    streaming_matches, streaming_failure = streaming

    matches = emby_matches + streaming_matches
    if not matches:
        message = streaming_failure or f"No content found for: {base_query}"
        return ContentSearchResult(success=False, message=message, query=base_query, matches=[])

    service_names = [m.service_name for m in matches]
    return ContentSearchResult(
        success=True,
        message=f"Found content on {len(matches)} service(s): {', '.join(service_names)}",
        query=base_query,
        matches=matches,
    )


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
```

Delete the old inline body (the helper now owns it). Existing failure-path tests assert the same messages — they must still pass unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_search_and_play.py -v`
Expected: all PASS (old failure-message tests included)

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright .
git add app/core/search_and_play.py tests/test_search_and_play.py tests/conftest.py
git commit -m "feat(emby): merge Emby library results into search_content

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Tool layer + chat wiring

**Files:**
- Modify: `backend/app/core/llm/tool_executor.py` (constructor ~line 168; `find_content`/`launch_on_roku` tool defs ~lines 91–161; `_find_content` ~line 232; `_launch_on_roku` ~line 247)
- Modify: `backend/app/api/chat.py` (`SYSTEM_PROMPT` ~line 22; `get_tool_executor` ~line 76)
- Test: `backend/tests/test_tool_executor.py`

**Interfaces:**
- Consumes: `EmbyClient` (Task 2), `EMBY_CHANNEL_ID` (Task 2), `search_content(..., emby_client=...)` (Task 5), `launch_on_roku(..., post_launch_key: str | None, resume_position_ticks: int | None)` (Task 3).
- Produces: `RokuECPToolExecutor(roku_ip, http_client, brave_client=None, emby_client=None)`; handler rule: channel 44191 → `post_launch_key=None` always; other channels default `"Select"`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_tool_executor.py` (add `from app.core.emby import EmbyClient` and reuse the module's `_tc` helper; import `EmbyItem` too):

```python
@pytest.fixture
def mock_emby_client() -> AsyncMock:
    client = AsyncMock(spec=EmbyClient)
    client.server_url = "http://emby.local:8096"
    return client


@pytest.mark.asyncio
async def test_launch_tool_emby_is_launch_only(mock_emby_client: AsyncMock) -> None:
    """Channel 44191 never gets a keypress, even if the model passes post_launch_key."""
    mock_http_client = AsyncMock()
    ok = MagicMock()
    ok.status_code = 200
    mock_http_client.post.return_value = ok

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, emby_client=mock_emby_client)
    result = await executor.execute_tool(
        _tc(
            "launch_on_roku",
            {"channel_id": 44191, "content_id": "3f9a1c", "post_launch_key": "Select"},
        )
    )

    assert '"success": true' in result
    assert mock_http_client.post.call_count == 1
    _, kwargs = mock_http_client.post.call_args
    assert kwargs["params"] == {"Command": "PlayNow", "ItemIds": "3f9a1c"}


@pytest.mark.asyncio
async def test_launch_tool_emby_resume_passthrough(mock_emby_client: AsyncMock) -> None:
    mock_http_client = AsyncMock()
    ok = MagicMock()
    ok.status_code = 200
    mock_http_client.post.return_value = ok

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, emby_client=mock_emby_client)
    await executor.execute_tool(
        _tc(
            "launch_on_roku",
            {"channel_id": 44191, "content_id": "3f9a1c", "resume_position_ticks": 12000000000},
        )
    )

    _, kwargs = mock_http_client.post.call_args
    assert kwargs["params"]["StartPositionTicks"] == "12000000000"


@pytest.mark.asyncio
async def test_launch_tool_streaming_defaults_select(mock_emby_client: AsyncMock) -> None:
    """Non-Emby launches keep the Select default when the model omits the key."""
    mock_http_client = AsyncMock()
    ok = MagicMock()
    ok.status_code = 200
    mock_http_client.post.return_value = ok

    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, emby_client=mock_emby_client)
    with patch("app.core.search_and_play.asyncio.sleep", new_callable=AsyncMock):
        await executor.execute_tool(_tc("launch_on_roku", {"channel_id": 2285, "content_id": "abc"}))

    assert mock_http_client.post.call_count == 2
    assert "/keypress/Select" in mock_http_client.post.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_find_content_uses_emby(mock_emby_client: AsyncMock) -> None:
    """find_content works with Emby alone (no Brave configured)."""
    mock_http_client = AsyncMock()
    mock_emby_client.search.return_value = [EmbyItem(item_id="m1", name="Heat", item_type="Movie")]

    executor = RokuECPToolExecutor(
        "192.168.1.100", mock_http_client, brave_client=None, emby_client=mock_emby_client
    )
    result = await executor.execute_tool(_tc("find_content", {"title": "Heat"}))

    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["matches"][0]["service_name"] == "emby"
    assert payload["matches"][0]["channel_id"] == 44191
    assert payload["matches"][0]["post_launch_key"] is None


@pytest.mark.asyncio
async def test_find_content_no_backends() -> None:
    mock_http_client = AsyncMock()
    executor = RokuECPToolExecutor("192.168.1.100", mock_http_client, brave_client=None, emby_client=None)

    result = await executor.execute_tool(_tc("find_content", {"title": "Heat"}))

    payload = json.loads(result)
    assert payload["success"] is False
```

Add `import json`, `patch` to the `unittest.mock` import line, and `from app.core.emby import EmbyClient, EmbyItem` to the test module imports if missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tool_executor.py -v -k "emby or backends or defaults_select"`
Expected: FAIL with `TypeError: RokuECPToolExecutor.__init__() got an unexpected keyword argument 'emby_client'`

- [ ] **Step 3: Implement in `backend/app/core/llm/tool_executor.py`**

Imports:

```python
from ..emby import EMBY_CHANNEL_ID, EmbyClient
```

(`EmbyClient` is used in a runtime default, so import it unconditionally, not under `TYPE_CHECKING`.)

Constructor:

```python
    def __init__(
        self,
        roku_ip: str,
        http_client: Any,
        brave_client: BraveSearchClient | None = None,
        emby_client: EmbyClient | None = None,
    ) -> None:
        ...
        self.emby_client = emby_client
```

(keep the rest of `__init__` as is).

`find_content` tool description — replace with:

```python
        description=(
            "Search the user's personal Emby library and streaming services "
            "(Netflix, Hulu, Disney+, Max, Apple TV+, Amazon Prime) for content and "
            "return all available matches with channel IDs, content IDs, media_type, "
            "post_launch_key, and resume_position_ticks (Emby only, when partially "
            "watched). Emby matches are listed first. Use this when you know the exact "
            "content to find. After calling this, use launch_on_roku with the returned "
            "values to play content."
        ),
```

`launch_on_roku` tool description — replace with:

```python
        description=(
            "Launch content on the Roku device. Call this after find_content with one of "
            "the returned matches. Provide the channel_id, content_id, and media_type from "
            "the find_content results. For streaming services also pass post_launch_key; "
            "the launch waits 2 seconds and presses that key. Emby (channel 44191) is "
            "launch-only and starts playback directly; pass resume_position_ticks from "
            "the match to resume where the user left off."
        ),
```

In the `launch_on_roku` schema, update `post_launch_key`'s description to `"Key to press after launch (streaming services only; ignored for Emby)"` and add after it:

```python
                "resume_position_ticks": {
                    "type": "integer",
                    "description": "Emby resume position in ticks (from find_content); omit to play from the start",
                },
```

`_find_content` — replace the Brave-only guard and pass the client through:

```python
    async def _find_content(self, arguments: dict[str, Any]) -> str:
        """Search the Emby library and streaming services for content."""
        if self.brave_client is None and self.emby_client is None:
            return json.dumps(
                {"success": False, "message": "No search backends configured (Brave Search or Emby).", "matches": []}
            )

        result = await search_content(
            title=arguments.get("title", ""),
            brave_client=self.brave_client,
            season=arguments.get("season"),
            episode=arguments.get("episode"),
            episode_title=arguments.get("episode_title"),
            media_type=arguments.get("media_type"),
            http_client=self.http_client,
            emby_client=self.emby_client,
        )
        return result.to_tool_result()
```

`_launch_on_roku` — replace with:

```python
    async def _launch_on_roku(self, arguments: dict[str, Any]) -> str:
        """Launch content on Roku given channel_id and content_id."""
        channel_id = arguments.get("channel_id")
        content_id = arguments.get("content_id")
        media_type = arguments.get("media_type", "movie")

        if not channel_id or not content_id:
            return json.dumps({"success": False, "message": "channel_id and content_id are required."})

        channel = int(channel_id)
        # Emby is launch-only: never press a key, whatever the model passed.
        if channel == EMBY_CHANNEL_ID:
            post_launch_key: str | None = None
        else:
            post_launch_key = arguments.get("post_launch_key") or "Select"

        resume_ticks = arguments.get("resume_position_ticks")

        result = await launch_on_roku(
            channel_id=channel,
            content_id=str(content_id),
            roku_base_url=self.base_url,
            http_client=self.http_client,
            media_type=media_type,
            post_launch_key=post_launch_key,
            resume_position_ticks=int(resume_ticks) if resume_ticks is not None else None,
        )
        return json.dumps({"success": result.success, "message": result.message})
```

- [ ] **Step 4: Implement in `backend/app/api/chat.py`**

Import:

```python
from ..core.emby import EmbyClient
```

In `get_tool_executor`, replace the `roku_ecp` branch's return:

```python
        emby_client = None
        if settings.emby.server_url and settings.emby.api_key:
            emby_client = EmbyClient(
                server_url=settings.emby.server_url,
                api_key=settings.emby.api_key.get_secret_value(),
                user_id=settings.emby.user_id,
                http_client=http_client,
            )
        return RokuECPToolExecutor(settings.roku.ip, http_client, brave_client, emby_client)
```

Replace `SYSTEM_PROMPT` with:

```python
SYSTEM_PROMPT = (
    "You are a helpful assistant that controls Roku devices. "
    "Use the available tools to help users find and play content.\n\n"
    "When a user asks to play content:\n"
    "1. If you're unsure about the exact title, season, or episode, use web_search "
    "to research it first.\n"
    "2. Once you know the exact content, call find_content to search the user's "
    "personal Emby library and streaming services.\n"
    "3. After find_content returns, present the available options to the user and "
    "let them choose where to play. Emby is the user's own server — list it first "
    "when it has the content. Do NOT automatically call launch_on_roku.\n"
    "4. When the user tells you which service to use, call launch_on_roku with that "
    "service's channel_id, content_id, and media_type. For Emby matches, omit "
    "post_launch_key and pass resume_position_ticks when the match has one, so "
    "playback continues where the user left off.\n\n"
    "For general questions or when you need information, use web_search.\n"
    "For direct Roku operations, use search_roku or get_roku_status."
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tool_executor.py tests/test_websocket.py tests/test_chat_simple.py -v`
Expected: all PASS

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright .
git add app/core/llm/tool_executor.py app/api/chat.py tests/test_tool_executor.py
git commit -m "feat(emby): wire Emby into find_content/launch_on_roku tools

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full gates + speclib re-record

**Files:**
- No source edits expected; `speclib.lock` updated by the CLI only.

**Interfaces:**
- Consumes: everything above.
- Produces: a recorded sync whose selections include Emby. `fixture_status` stays `skip`: the 2 Emby fixtures now run, but YouTube (spec v1.4.1+, not selected in this repo) still has fixtures excluded at materialization, and speclib's rule is "excluded any → skip, never pass".

- [ ] **Step 1: Run every gate**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright .
uv run pytest
```

Expected: all clean, full suite green. Fix anything that fails before proceeding (root cause, not symptom).

- [ ] **Step 2: Re-record the speclib sync**

Both Emby playback cases now run, but YouTube's fixtures remain excluded at materialization (channel not selected), so the honest status is still `skip`:

```bash
speclib sync --record roku-deeplink \
  --test-command "uv run pytest tests/test_roku_deeplink_fixtures.py" \
  --fixture-status skip \
  --selections "language: python; tracks: live streaming.py (match_url_full) + search_and_play.py (launch_on_roku, search_content verification per spec §11); channels: Netflix, Disney+, HBO Max, Prime Video, Hulu, Apple TV+, Emby (self-hosted, launch-only); excludes: YouTube (not selected)"
```

- [ ] **Step 3: Verify and commit**

```bash
speclib verify
git add speclib.lock
git commit -m "chore(speclib): record Emby in roku-deeplink selections, fixtures pass

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(The lock lives at `backend/speclib.lock` and these commands run from `backend/`.)

- [ ] **Step 4: Report**

Summarize: files created/modified, every gate command with its result, fixture status now `pass`, and remind the user to fill in the real `emby:` values in `config.yml` (server_url, api_key, user_id) before using it.
