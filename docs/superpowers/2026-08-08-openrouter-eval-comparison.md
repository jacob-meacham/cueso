# OpenRouter Eval Comparison — Claude Sonnet 4.5 vs GLM 5.2 Nitro

**Date:** 2026-08-08
**Branch:** worktree-openrouter-consolidation
**Context:** Task 4 of the OpenRouter consolidation (see
`docs/superpowers/specs/2026-08-08-openrouter-consolidation-design.md` and
`docs/superpowers/plans/2026-08-08-openrouter-consolidation.md`). Tasks 1–3
made OpenRouter the sole LLM gateway; task 4 was the live end-to-end
validation run — the full eval suite through OpenRouter once with each
model, recorded below under "Pre-fix run". Task 5 root-caused and fixed the
bug that run surfaced; see "Post-fix targeted rerun" and "Status" below.

## Status: root cause fixed

Claude's two failures in the pre-fix run below were **not a model-quality
gap** — they were caused by a bug in our own `OpenRouterProvider.generate_stream`
(`backend/app/core/llm/providers/openrouter.py`): OpenRouter's Claude route
can emit `finish_reason` on more than one chunk for the same turn, and the
finalization block re-appended the accumulated tool calls on each such
chunk without clearing its accumulator state, producing duplicate
(non-unique) `tool_use` ids. Resending that history for a second LLM
round-trip is what Anthropic's backend validation rejected with the `400:
tool_use ids must be unique` error seen below.

Fixed in commit `ae4fd2c` ("fix(llm): finalize streamed tool calls exactly
once to keep tool_use ids unique"), with a regression test added/hardened in
`ae4fd2c` and `a9326aa`. Full root-cause evidence (live diagnostic chunk
log, failing/passing test output, gate results) is in
`.superpowers/sdd/2026-08-08-openrouter-consolidation/task-5-report.md`.

## Pre-fix run

Both runs below used the same backend build (worktree HEAD at the time of
the run, pre-fix), the same `backend/config.yml` except for `llm.model`, and
the same eval suite (`cli/evals/run.py`, 6 prompts), run exactly once per
model against a freshly started dev server. No production code changed
between runs. This section is kept as the original historical record of the
task 4 run; see "Post-fix targeted rerun" below for what changed after the
fix.

### Results

| # | Eval | Claude (`anthropic/claude-sonnet-4.5`) | GLM (`z-ai/glm-5.2:nitro`) |
|---|------|:---:|:---:|
| 1 | Play that Rick and Morty episode with the snakes | FAIL | PASS |
| 2 | Play Inception on my Roku | PASS | PASS |
| 3 | Can you put on The Bear? | PASS | PASS |
| 4 | Play Stranger Things season 4 episode 1 | PASS | PASS |
| 5 | Play the episode of Breaking Bad where they blow up the lab | FAIL | PASS |
| 6 | Put on Severance season 2 | PASS | PASS |
| **Total** | | **4/6** | **6/6** |
| **Wall clock (full suite)** | | **~22s** | **~14s** |

Per-eval latency (user message received → `final`/error event, from server logs):

| # | Claude | GLM |
|---|---|---|
| 1 | 4.9s (error) | 4.2s (2 iterations) |
| 2 | 2.7s | 1.1s |
| 3 | 2.6s | 1.2s |
| 4 | 3.0s | 1.9s |
| 5 | 5.7s (error) | 4.2s (2 iterations) |
| 6 | 3.2s | 1.9s |

GLM 5.2 Nitro was faster on every eval, including the two that required a
second LLM round-trip (web research → find_content).

### Notable behavioral differences

**Both models issued two parallel tool calls per turn on every eval**, with
identical arguments (e.g. `find_content` called twice with the same query,
`web_search` called twice with the same query). This looks like an existing
pattern independent of provider/model choice — not something introduced by
this comparison — but it's a real inefficiency (2x tool executions, 2x
downstream Brave/search API calls) worth a separate look.

**Claude failed both evals that required a second LLM round-trip** (the two
"research" prompts where the model must call `web_search`, then call
`find_content` with what it learned). Both failures were the identical error,
returned by every backend OpenRouter retried across (Azure, Amazon Bedrock,
Google Vertex, and Anthropic directly — twice):

```
Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400,
'metadata': {'raw': '{"type":"error","error":{"type":"invalid_request_error",
"message":"messages.1.content.2: `tool_use` ids must be unique"} ...
```

Because every backend OpenRouter tried rejected the *same* request with the
*same* complaint, the duplicate `tool_use` id must already be present in the
message payload we sent — i.e. this reproduced identically regardless of
which infrastructure ultimately served the Anthropic model. Server logs
confirm the assistant turn that triggered it contained exactly two tool
calls (`Assistant message added; tool_calls=2`) — the same "two identical
parallel tool calls" pattern GLM also exhibited on every eval. The
difference is that GLM's two parallel tool calls came back with distinct
ids (no collision), while Claude's did not, on both evals where a second
round-trip was needed. The two evals that failed for Claude are exactly the
two evals where the loop continues into a second LLM call (`find_content` is
in `pause_after` and stops the loop before resending history; `web_search`
is not, so the loop resends the tool results — and it's only on that resend
that the duplicate-id message is rejected).

This points at the OpenRouter provider's streaming tool-call id capture
(`generate_stream` in `backend/app/core/llm/providers/openrouter.py`,
`_tool_ids` dict keyed by delta index) not reliably producing a unique id per
parallel tool call when the underlying model is routed to an Anthropic-family
backend through OpenRouter. It did not reproduce with GLM in this run. This
was not treated as an infra flake (identical, deterministic-looking failure
on both affected evals, consistent with a specific code path) and was not
rerun, per the task's guidance to avoid burning eval budget on non-flake
failures — this is a candidate follow-up bug, not a retry candidate.

**Response style:** Claude narrated every step with a sentence before each
tool call (e.g. "I'll help you find that Rick and Morty episode..."). GLM
was terser on simple, single-step lookups (no preamble before `find_content`
on evals 2–4, 6) but produced comparably rich narration on the two research
evals, including confident, specific answers synthesized from search results
plus its own knowledge (e.g. identifying "Rattlestar Ricklactica" S4E5 for
eval 1 and "Face Off" S4E13 for eval 5 — Claude never reached this point on
either eval due to the error above, so no direct quality comparison is
possible for those two).

No malformed tool arguments, wrong tool selection, or Roku-launch failures
were observed for either model on the evals that completed.

*(Historical note: the "candidate follow-up bug" flagged above was
root-caused and fixed in task 5 — see "Status: root cause fixed" at the top
of this doc. The analysis in this "Pre-fix run" section is left unedited as
the original record of what was observed at the time.)*

## Post-fix targeted rerun

After the fix (commit `ae4fd2c`, hardened by `a9326aa`), `backend/config.yml`
was set back to `anthropic/claude-sonnet-4.5` and **only the two originally
failed evals (1 and 5)** were rerun against a freshly started dev server, via
`cd cli && uv run python -m evals 1 5 --url ws://localhost:8484/ws/chat`:

| # | Eval | Claude, post-fix |
|---|------|:---:|
| 1 | Play that Rick and Morty episode with the snakes | PASS (2 iterations) |
| 5 | Play the episode of Breaking Bad where they blow up the lab | PASS (2 iterations) |

Both completed a second LLM round-trip (`web_search` → `find_content`) with
no `tool_use ids must be unique` error — the exact failure mode from the
pre-fix run is gone.

**This is a targeted 2-eval rerun, not a full fresh 6-eval Claude pass.**
Evals 2, 3, 4, and 6 were not rerun after the fix — they already passed in
the pre-fix run and are unaffected by this bug (they never trigger a second
round-trip), but a full fresh 6/6 confirmation for Claude post-fix has not
been done. Combining the pre-fix full run's 4 unaffected passes with this
targeted rerun's 2 passes gives Claude an effective 6/6 across the two runs
combined — not the same evidentiary strength as a single fresh full-suite
run, but sufficient to say the specific defect that caused both failures is
resolved.

## Recommendation

**Both models now pass all 6 evals through OpenRouter** (Claude: 4/6 in the
original full run + 2/2 in the targeted post-fix rerun, combining to an
effective 6/6; GLM: 6/6 in a single full run). The original recommendation
to flip the default to GLM was made to route around a live bug, not because
of a genuine model-quality gap — that bug is now fixed, so the choice is a
real tradeoff rather than a reliability verdict:

- **GLM 5.2 Nitro** was faster on every eval in the pre-fix run (~14s vs
  ~22s wall clock for the full suite), with no observed quality issues on
  the prompts tested. (Relative per-call pricing between the two models was
  not measured in either eval run — no cost claim is made here.)
- **Claude Sonnet 4.5** was the system's prior default and the model this
  system was originally designed around. In the post-fix targeted rerun it
  passed both evals 1 and 5 by the eval harness's own pass criteria (a
  `find_content` match reached after a `web_search`-informed second
  round-trip). Response content from that rerun was not captured for
  comparison, so no claim is made here about the quality of Claude's
  post-fix research answers specifically — only that the id-collision
  defect no longer blocks the round-trip. (The "Rattlestar Ricklactica" /
  "Face Off" identifications mentioned elsewhere in this doc are GLM's,
  from the pre-fix run — see "Notable behavioral differences" above.)
- Both models exhibited the same pre-existing, separate inefficiency
  (duplicate parallel tool calls per turn) noted above — not a
  differentiator between them.

**Final call belongs to the user/product owner** — this is a
speed-vs-familiarity tradeoff (per-call cost was not measured for either
model in these runs) between two models that both now work correctly
through OpenRouter, not a bug-driven decision. If reverting to Claude as the
default, note the confirmation above is a targeted 2-eval rerun; a fresh
full 6-eval Claude run would give higher confidence before shipping that
change.

**Follow-up already completed (was "suggested" pre-fix, now done):** the
`_tool_ids`/`_tool_arg_buffers`/`_tool_names` accumulator state in
`OpenRouterProvider.generate_stream` is now cleared after each finalization,
so a repeat `finish_reason` chunk is a no-op instead of re-appending
already-finalized tool calls. See commit `ae4fd2c` and
`.superpowers/sdd/2026-08-08-openrouter-consolidation/task-5-report.md` for
full details.
