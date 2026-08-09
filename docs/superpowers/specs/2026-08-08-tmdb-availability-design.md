# Design: TMDB Watch-Providers Availability Filter

**Date:** 2026-08-08
**Status:** Approved

## Problem

`find_content` presents its matches to the user as playable options, but a URL
match does not prove playability. The concrete case: netflix.com serves
`/title/{id}` pages for catalog titles it cannot stream (Bye Bye Birdie →
`/title/342088`, a DVD-era placeholder). The roku-deeplink v1.5.0 probe solves
this for Prime Video ASINs, but no URL-level or cheap HTTP discriminator exists
for Netflix (spec §11, Known Limitations). Picking such a match opens Netflix
to a dead page.

TMDB's watch-providers endpoint (JustWatch-licensed, free API key) knows which
services actually stream a title per region. This design uses it as an
availability oracle to filter `search_content` matches.

## Decisions (from brainstorming)

- **Role: filter, not annotate or discover.** A match is dropped only when the
  oracle affirmatively says the title is not streamable on that service. Every
  other outcome keeps the match (fail open).
- **Region:** `tmdb.region` config key, default `"US"`.
- **API key:** user-supplied v3 key in `config.yml` under `tmdb.api_key`
  (already present). No key → oracle inert, pipeline unchanged.
- **Speed budget:** measured 250–370 ms for resolve + parallel provider
  lookups on a warm connection. The oracle depends only on the title, so it
  runs concurrently with the Brave search (~300–800 ms); net added latency is
  ~0 in the typical case, capped by a 2 s oracle timeout (fail open).

## Architecture

New module `app/core/tmdb.py`, mirroring `brave_search.py`:

```
class TMDBClient:
    def __init__(api_key, region="US", http_client=None)
    async def get_streamable_services(title, tv_only=False) -> set[str] | None
    async def close()
```

Contract of `get_streamable_services`:

- `None` → "no opinion": no plausible candidates, API error, or timeout.
  Callers must treat `None` as "do not filter".
- `set[str]` → cueso service names that can play the title in the configured
  region. May be empty (title known, streamable nowhere we support).

Pipeline change in `search_content` (`app/core/search_and_play.py`):

1. Start the oracle as an `asyncio.Task` immediately (title is already known),
   in parallel with the Brave search.
2. Run Brave → URL match → probe verification exactly as today.
3. Await the oracle with `asyncio.wait_for(…, timeout=2.0)`; on timeout or
   exception, treat as `None`.
4. If the oracle returned a set, drop matches whose `service_name` is not in
   it. Log each drop; append a note to the result `message`, e.g.
   `"filtered netflix (not streamable per TMDB)"`, so the LLM can explain the
   missing option.

The tool-result shape is unchanged.

## Resolution & Provider Semantics

- **Search:** `GET /3/search/multi?query={title}`. Keep results with
  `media_type` in `{movie, tv}`; when the `find_content` call carried a season
  or episode, restrict to `tv`.
- **Plausibility:** normalized-title equality — casefold, strip punctuation,
  collapse whitespace; compare against `title`/`name`. Cap at 3 candidates.
  Measured rationale: naive top-3 lets "Masha and the Bear" (Netflix) rescue
  Netflix for "The Bear"; exact normalized match excludes it while still
  keeping "Bye-Bye Birdie" and both Bye Bye Birdie adaptations (1963, 1995).
- **Providers:** `GET /3/{media_type}/{id}/watch/providers`, fetched
  concurrently for all candidates. Read the `results[region]` block; union
  across candidates and across ALL monetization buckets (`flatrate`, `free`,
  `ads`, `rent`, `buy`). Rent/buy counts as playable: the Roku deep link opens
  the title page where renting works (the actual Bye Bye Birdie situation on
  Prime).
- **Union-across-candidates** is deliberate fail-open bias: with multiple
  adaptations, a service survives if ANY adaptation streams there.

**Provider ID → service mapping** (module-level dict; several IDs per service;
IDs are TMDB-stable):

| cueso service | TMDB provider IDs |
|---|---|
| `netflix` | 8 (Netflix), 1796 (Netflix Standard with Ads) |
| `amazon_prime` | 9 (Amazon Prime Video), 10 (Amazon Video rent/buy), 2100 (Prime with Ads) |
| `disney_plus` | 337 (Disney Plus) |
| `hulu` | 15 (Hulu) |
| `max` | 1899 (Max/HBO Max — verified live), 384 (HBO Max pre-rebrand legacy) |
| `apple_tv_plus` | 350 (Apple TV+), 2 (Apple TV rent/buy store) |

Unmapped provider IDs are ignored. A cueso service with no mapped ID present
in any candidate's region block is "not streamable" — the affirmative absence
that permits dropping.

## Configuration

```yaml
tmdb:
  api_key: <v3 key>   # SecretStr | None; None disables the oracle
  region: US          # default
```

`TMDBConfig` beside `BraveConfig` in `app/core/config.py`. The
`RokuECPToolExecutor` constructs a `TMDBClient` when a key is configured
(sharing its `http_client`) and passes it to `search_content` as
`tmdb_client: TMDBClient | None = None` — same wiring pattern as the probe's
`http_client`.

## Error Handling (all fail open)

| Condition | Behavior |
|---|---|
| No `tmdb.api_key` configured | No client constructed; no filtering |
| Oracle timeout (> 2 s) | Filter skipped, warning logged |
| TMDB HTTP/network error | `None`, warning logged |
| No plausible candidates | `None` — never filter on "title unknown" |
| Candidates but empty region block | Providers unknown for region → contributes nothing; if ALL candidates lack the region block, treat as `None` |

The last row matters: an empty `results[region]` means TMDB lacks region data,
not that nothing streams it.

## Testing

Mocked-TMDB unit tests (`tests/test_tmdb.py`, plus `search_content` cases in
`tests/test_search_and_play.py`):

- Netflix placeholder dropped when oracle set excludes `netflix`.
- "Masha and the Bear" collision excluded by normalization.
- Adaptation union keeps `amazon_prime` for Bye Bye Birdie.
- Every fail-open path: no client, timeout, HTTP error, no candidates,
  region block absent.
- `tv_only` restriction when season/episode present.
- Message notes appended for dropped services.

Live smoke: the benchmark script (3 titles, timing + provider sets) kept as a
manual check.

## Attribution

TMDB terms require crediting: add a "search data powered by TMDB and
JustWatch" line to the README. No logo requirements for a personal project.

## Out of Scope

- Caching (volume is one lookup per voice command; TMDB limits are ~50 rps).
- Annotating matches instead of filtering (revisit if fail-open drops prove
  too conservative or too aggressive).
- roku-deeplink spec changes — this is cueso-local; at most §11 may later gain
  a one-line "an external availability oracle may further filter candidates".
- Season-level availability (TMDB provider data is title-level).
