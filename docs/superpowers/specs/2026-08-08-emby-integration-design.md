# Emby Integration Design

**Date:** 2026-08-08
**Status:** Approved (pending implementation)

## Goal

Make the user's self-hosted Emby server a first-class playback target in cueso:
searchable through the normal `find_content` flow and playable on Roku via the
Emby channel, including resume ("continue watching"). This also brings the
roku-deeplink spec's Emby channel (44191) into the tracked speclib surface, so
its two playback fixtures run against live code and `fixture_status` can become
`pass`.

## Background

The live pipeline is `find_content` → `search_content()` (Brave web search with
`site:` filters → URL regex match) → `launch_on_roku()` (launch → wait 2000ms →
keypress). Emby fits neither half today:

- Emby is self-hosted, so Brave cannot find its content. Search must query the
  Emby server's REST API directly.
- Emby playback is launch-only with channel-specific params
  (`Command=PlayNow&ItemIds={id}`, optional `StartPositionTicks`), no
  post-launch keypress. The current `launch_on_roku()` hardcodes
  `contentId`/`mediaType` params and always presses a key.

The roku-deeplink spec covers the playback half (Emby channel 44191,
descriptor-addressed, launch-only) and explicitly leaves library search to the
caller. Search is therefore new cueso feature work; playback is spec-covered.

## Decisions Made

1. **Scope:** full search + playback now (not playback-only, which would be
   unreachable code until search existed).
2. **Tool surface:** Emby results are merged into the existing `find_content`
   tool, not exposed as a separate tool. Emby matches are listed first (local,
   free, fastest to start).
3. **Resume:** search surfaces `resume_position_ticks` from Emby's user-scoped
   API so playback can continue mid-episode.
4. **Shape:** dedicated `app/core/emby.py` client module mirroring
   `brave_search.py` (rejected: modeling Emby as a `StreamingService` — the
   registry is URL-centric and every field would be dead weight; rejected: a
   fully separate pipeline — duplicated plumbing and it would split spec
   Function 2 across two implementations).

## Architecture

```
find_content ──> search_content()
                   ├─ EmbyClient.search()   (local library, concurrent)
                   └─ Brave + URL match     (streaming, existing)
                        └─> unified matches: Emby first, then streaming
LLM presents options → user picks → launch_on_roku tool
                   └─ launch_on_roku(): Emby → single launch (PlayNow, optional resume)
                                        others → launch → wait 2s → keypress
```

`streaming.py` (URL matching) is untouched.

## Components

### `app/core/emby.py` (new)

- `EmbyItem` dataclass: `item_id`, `name`, `item_type` (`Movie` | `Series` |
  `Episode`), `resume_position_ticks: int | None` (None when unwatched or
  fully watched).
- `EmbyError` exception, mirroring `BraveSearchError`.
- `EmbyClient(server_url, api_key, user_id, http_client=None)` with
  `async search(title, season=None, episode=None) -> list[EmbyItem]`:
  - `GET {server}/emby/Users/{user_id}/Items` with `SearchTerm={title}`,
    `IncludeItemTypes=Movie,Series`, `Recursive=true`; auth via the
    `X-Emby-Token` header. The user-scoped endpoint returns
    `UserData.PlaybackPositionTicks` in the same response.
  - When `season`/`episode` are provided and a `Series` matches, resolve the
    concrete episode via
    `GET {server}/emby/Shows/{seriesId}/Episodes?Season={season}&UserId={user_id}`
    and return that `Episode` item instead of the series.
  - 10s timeout, same client-ownership pattern as `BraveSearchClient`
    (optional injected `httpx.AsyncClient`, `close()` if owned).

### `app/core/config.py`

- `EmbyConfig(BaseModel)`: `server_url: str = ""`,
  `api_key: SecretStr | None = None`, `user_id: str = ""`.
- `Settings.emby: EmbyConfig`. Emby is active only when `server_url` and
  `api_key` are both set (same optional-client pattern as Brave).
- `config.yml.example` documents the new section, including how to find the
  Emby user id (server dashboard → Users, or `/emby/Users` API).

### `app/core/search_and_play.py`

- `ContentMatch.post_launch_key` becomes `str | None` (None = launch-only)
  and the dataclass gains `resume_position_ticks: int | None = None`.
- `search_content(..., emby_client: EmbyClient | None = None)`:
  - Runs the Emby search and the Brave search concurrently
    (`asyncio.gather`).
  - Maps Emby item types → media types: `Movie` → `movie`, `Series` →
    `series`, `Episode` → `episode`.
  - Emby matches become `ContentMatch(service_name="emby",
    channel_id=44191, content_id=item_id, post_launch_key=None,
    resume_position_ticks=…)`; `source_url` points at the item in the Emby
    web UI (`{server_url}/web/index.html#!/item?id={item_id}`).
  - Emby matches are prepended to the streaming matches.
- `launch_on_roku(...)` implements the spec's Emby branch (spec Function 2
  stays one live function):
  - New params: `post_launch_key: str | None` (existing default `"Select"`
    kept, so the `/roku/launch` REST endpoint and current callers are
    unchanged) and `resume_position_ticks: int | None = None`.
  - Channel 44191 → launch params `Command=PlayNow&ItemIds={content_id}`
    plus `StartPositionTicks={ticks}` when resuming; all other channels keep
    `contentId={id}&mediaType={type}`.
  - `post_launch_key is None` → return after the launch POST (no wait, no
    keypress).

### Tool layer (`app/core/llm/tool_executor.py`, `app/api/chat.py`)

- `RokuECPToolExecutor.__init__` gains `emby_client: EmbyClient | None`;
  constructed from settings where the executor is built in `api/chat.py`.
- `find_content` description mentions the local Emby library and that Emby
  results come first; handler passes the client through.
- `launch_on_roku` tool schema: `post_launch_key` no longer implies
  `Play`/`Select` only (omitted for Emby); new optional integer
  `resume_position_ticks`. Description explains Emby is launch-only and plays
  directly.
- Handler defaulting rule (important): today the handler defaults a missing
  `post_launch_key` to `"Select"`, which would wrongly press Select after an
  Emby launch. New rule: channel 44191 → always `None` (launch-only,
  regardless of what the model passed); any other channel with the argument
  missing → `"Select"` (preserves current behavior).
- System prompt: note that Emby is the user's own server and should be
  offered first when it has the content, and that resume is available.

## Error Handling

- Emby down/unreachable/misconfigured URL: `EmbyError` and timeouts are
  caught inside `search_content`, logged as a warning, and streaming results
  are returned alone. A dead local server must never break search.
- Emby unconfigured: `emby_client` is None; skipped silently (same as Brave).
- Launch errors: unchanged — `LaunchResult` already models connection
  failures and non-200s; the Emby branch reuses it.

## Testing

- `tests/test_emby.py` (new): mocked-HTTP unit tests for `EmbyClient` — auth
  header, query params, item parsing, resume ticks, episode resolution,
  error mapping.
- `tests/test_search_and_play.py`: merge ordering (Emby first), Emby-failure
  degradation, Emby-unconfigured behavior, resume passthrough.
- `tests/test_roku_deeplink_fixtures.py`: the 2 Emby playback fixtures now
  run against `launch_on_roku()` (single POST, `PlayNow` params, resume
  variant, no sleep/keypress); `test_emby_playback_fixtures_are_excluded` is
  removed.
- Gates: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyright .`, `uv run pytest`.

## Speclib Bookkeeping

After implementation passes all gates, re-record:

```
speclib sync --record roku-deeplink \
  --test-command "uv run pytest tests/test_roku_deeplink_fixtures.py" \
  --fixture-status pass \
  --selections "language: python; tracks: live streaming.py (match_url_full) + search_and_play.py (launch_on_roku); channels: Netflix, Disney+, HBO Max, Prime Video, Hulu, Apple TV+, Emby (self-hosted, launch-only)"
```

`--fixture-status pass` is only valid because every fixture, including both
Emby cases, will then run. The CLI owns `speclib.toml`/`speclib.lock`.

## Out of Scope

- Emby authentication schemes other than API key (no username/password flow).
- Multi-user Emby resume (single configured `user_id`).
- Live TV, music, collections — only Movie/Series/Episode.
- Changes to `streaming.py` or the URL-matching path.
