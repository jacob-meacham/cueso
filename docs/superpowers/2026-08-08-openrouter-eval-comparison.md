# OpenRouter Eval Comparison — Claude Sonnet 4.5 vs GLM 5.2 Nitro

**Date:** 2026-08-08
**Branch:** worktree-openrouter-consolidation
**Context:** Task 4 of the OpenRouter consolidation (see
`docs/superpowers/specs/2026-08-08-openrouter-consolidation-design.md` and
`docs/superpowers/plans/2026-08-08-openrouter-consolidation.md`). Tasks 1–3
made OpenRouter the sole LLM gateway; this is the live end-to-end validation
run — the full eval suite through OpenRouter once with each model.

Both runs used the same backend build (worktree HEAD at the time of the run),
the same `backend/config.yml` except for `llm.model`, and the same eval suite
(`cli/evals/run.py`, 6 prompts), run exactly once per model against a freshly
started dev server. No production code changed between runs.

## Results

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

## Notable behavioral differences

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

## Recommendation

**Flip the default to `z-ai/glm-5.2:nitro` for now.** In this run, GLM passed
100% of the suite and was faster on every eval, while Claude failed exactly
the two evals that require a multi-step tool loop — the core "research an
ambiguous request, then find it on a streaming service" use case this system
is built around — due to what looks like a reproducible OpenRouter/Anthropic
tool-call-id bug rather than a model-quality gap. Shipping Claude as the
default in its current state means roughly a third of realistic multi-step
requests are expected to error out.

This recommendation is based on a single run per model (evals cost real
money, so the suite was run once each per the task constraints) and should be
revisited: if the duplicate-`tool_use`-id issue is root-caused and fixed
(likely in `OpenRouterProvider.generate_stream`'s per-index id tracking, or
possibly an upstream OpenRouter behavior worth reporting to them), Claude
Sonnet 4.5 should be re-evaluated as the default — its output quality on the
evals it did complete was on par with or more thorough than GLM's, and it's
the model this system was designed around. Until then, GLM 5.2 Nitro is the
more reliable choice for the multi-step tool-calling path.

**Suggested follow-up (separate from this task, no code changed here):**
investigate `_tool_ids` assignment in
`backend/app/core/llm/providers/openrouter.py`'s `generate_stream` for the
case of two parallel tool-call deltas in one Anthropic-backed response, and
consider a defensive fix (e.g. de-duplicating/regenerating tool-call ids
before they're echoed back in the next request) regardless of root cause.
