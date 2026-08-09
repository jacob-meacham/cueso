# OpenRouter Consolidation — Design

**Date:** 2026-08-08
**Status:** Approved
**Branch:** worktree-openrouter-consolidation

## Goal

Consolidate all LLM access in the Cueso backend onto OpenRouter. OpenRouter becomes
the single gateway: the default model stays a Claude Sonnet model (routed through
OpenRouter), and switching to alternatives like GLM 5.2 Nitro becomes a one-line
config change. The direct `anthropic` SDK dependency and both existing provider
modules' duplication go away.

## Context

- The backend selects an `LLMProvider` implementation in `api/chat.py`
  (`get_llm_provider`) based on `llm.provider` in config. Two implementations
  exist: `providers/anthropic.py` and `providers/openai.py`.
- OpenRouter speaks the OpenAI chat-completions API. The existing
  `OpenAIProvider` already implements correct message conversion, tool-schema
  conversion, streaming delta handling, and tool-call argument accumulation for
  that API. It lacks only a configurable `base_url`.
- OpenRouter serves Anthropic models (e.g. `anthropic/claude-sonnet-4.5`), so
  consolidating loses no model access.

## Decisions

1. **End state:** single provider, OpenRouter only. `anthropic.py` and the
   `anthropic` dependency are deleted.
2. **Default model:** Claude Sonnet via OpenRouter (`anthropic/claude-sonnet-4.5`)
   — behavior stays stable; GLM 5.2 Nitro is a config flip away.
3. **Validation bar:** unit tests + lint + typecheck pass, and the eval suite
   runs live through OpenRouter twice — once with the Claude default, once with
   GLM 5.2 Nitro — producing a quality comparison.
4. **Keep the `LLMProvider` ABC.** The provider pattern stays documented and
   tested; a future second backend remains an add, not a refactor.

## Design

### 1. Provider module

- `backend/app/core/llm/providers/openai.py` → renamed to
  `backend/app/core/llm/providers/openrouter.py`; class `OpenAIProvider` →
  `OpenRouterProvider`.
- All message conversion, tool conversion, and streaming logic carries over
  unchanged.
- Constructor signature:
  `__init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai/api/v1")`.
  `base_url` is passed to `AsyncOpenAI`, plus
  `default_headers={"X-Title": "Cueso"}` for OpenRouter dashboard attribution.
- `backend/app/core/llm/providers/anthropic.py` is deleted.
- `LLMProvider` ABC (`provider.py`) unchanged.

### 2. Streaming robustness

OpenRouter can emit SSE chunks with an empty `choices` array (e.g. trailing
usage frames). The current loop indexes `chunk.choices[0]` unguarded. Add
`if not chunk.choices: continue` at the top of the stream loop in
`generate_stream`. The non-streaming `_parse_response` stays as-is — an
empty-choices non-streaming response is a genuine API error, not a normal frame.
Reasoning deltas from models like GLM (`delta.reasoning`) are ignored — the
existing code already only reads `delta.content` and `delta.tool_calls`.

### 3. Configuration

`LLMConfig` in `backend/app/core/config.py` becomes:

```python
class LLMConfig(BaseModel):
    provider: str = "openrouter"
    api_key: SecretStr | None = None
    model: str = "anthropic/claude-sonnet-4.5"
    base_url: str = "https://openrouter.ai/api/v1"
```

- `get_llm_provider` in `api/chat.py` supports only `"openrouter"`; any other
  value raises `ValueError` (same error style as today).
- `llm.model` now takes OpenRouter model slugs.
- `base_url` is configurable so the same provider can later target any
  OpenAI-compatible endpoint (Ollama, vLLM, …).
- `backend/config.yml.example` updated to match. The developer's local
  `backend/config.yml` must be updated with an OpenRouter API key before live
  runs (manual step — secret not committed).
- Env-var overrides (`LLM__MODEL`, etc.) continue to work unchanged via
  Pydantic Settings.

### 4. Dependencies

- Remove `anthropic` from `backend/pyproject.toml`.
- Keep `openai` — it is the HTTP client used to talk to OpenRouter.

### 5. Tests and docs

- `tests/test_providers.py`: delete the Anthropic test classes; rename OpenAI
  classes to OpenRouter (conversion-logic tests remain valid). Add tests for:
  - `base_url` and default headers passed to `AsyncOpenAI`
  - empty-`choices` chunk skipped without error in `generate_stream`
  - `get_llm_provider` raises on non-`openrouter` provider values
- `tests/test_config.py`: update default expectations (provider, model,
  base_url).
- `CLAUDE.md` architecture section: providers list now `openrouter.py` only;
  provider-pattern wording stays.

### 6. Validation

1. `uv run pytest`, `uv run ruff check .`, `uv run pyright .` all clean.
2. Live validation with dev server running:
   - `./evals.sh` with `llm.model: anthropic/claude-sonnet-4.5`
   - `./evals.sh` with GLM 5.2 Nitro (exact slug confirmed against
     OpenRouter's live model list at implementation time; expected
     `z-ai/glm-5.2:nitro`)
   - Report the comparison; the default-model choice can be revisited based
     on results.

## Error handling

- Missing API key: existing `ValueError` in `get_llm_provider` stands, message
  updated to reference an OpenRouter key.
- Malformed tool-call JSON from the model: existing `{"_raw": ...}` fallback
  carries over unchanged.
- OpenRouter/upstream errors surface as `openai` SDK exceptions, identical to
  the current OpenAI path; no new handling layer.

## Out of scope

- OpenRouter provider-routing preferences (`extra_body` provider pinning),
  reasoning-effort controls, prompt caching — none needed until an eval shows
  otherwise.
- Frontend/CLI changes: none required; the WebSocket contract is unchanged.
- Session store or types changes: none.
