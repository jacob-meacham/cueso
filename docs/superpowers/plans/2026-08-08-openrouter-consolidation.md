# OpenRouter Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all LLM access onto OpenRouter: one `OpenRouterProvider` (OpenAI-compatible), delete the direct Anthropic provider and SDK, default model `anthropic/claude-sonnet-4.5`, validated live against both Claude and GLM 5.2 Nitro.

**Architecture:** The existing `OpenAIProvider` already speaks OpenRouter's wire format (OpenAI chat-completions). We rename it to `OpenRouterProvider`, add a configurable `base_url` + attribution header + an empty-`choices` stream guard, point config at OpenRouter, then delete `anthropic.py` and its dependency. The `LLMProvider` ABC and all session/tool-executor code are untouched.

**Tech Stack:** Python 3.13, FastAPI, `openai` SDK (as OpenRouter client), Pydantic Settings, pytest + pytest-asyncio, uv.

**Spec:** `docs/superpowers/specs/2026-08-08-openrouter-consolidation-design.md`

## Global Constraints

- All backend commands run from `backend/` in the worktree: `/code/jacob/cueso/.claude/worktrees/openrouter-consolidation/backend`
- Default provider string: `"openrouter"`; default model: `"anthropic/claude-sonnet-4.5"`; default base URL: `"https://openrouter.ai/api/v1"`
- Attribution header: `{"X-Title": "Cueso"}` on the `AsyncOpenAI` client
- `openai` dependency stays; `anthropic` is removed; no new dependencies
- Lint/type gates: `uv run ruff check .` and `uv run pyright .` (strict) must be clean after every task
- `config.yml` is gitignored (holds secrets) — never commit it
- Note: `httpx.URL` normalizes base URLs with a trailing slash — `str(client.base_url)` returns `"https://openrouter.ai/api/v1/"` (verified on openai 1.102.0)
- Async tests use the `@pytest.mark.asyncio` decorator (no asyncio_mode auto in this repo)

---

### Task 1: OpenRouterProvider module

Rename `providers/openai.py` → `providers/openrouter.py` with `base_url` support, attribution header, and an empty-`choices` stream guard. All message/tool conversion and streaming accumulation logic is kept byte-for-byte.

**Files:**
- Rename: `backend/app/core/llm/providers/openai.py` → `backend/app/core/llm/providers/openrouter.py`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Consumes: `LLMProvider` ABC from `app/core/llm/provider.py` (unchanged); `Message`, `MessageRole`, `SessionConfig`, `StreamResult`, `Tool`, `ToolCall` from `app/core/llm/types.py` (unchanged)
- Produces: `class OpenRouterProvider(LLMProvider)` in `app.core.llm.providers.openrouter` with `__init__(self, api_key: str, model: str, base_url: str = OPENROUTER_BASE_URL)` and module constant `OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"`. Task 3 imports exactly these names.

- [ ] **Step 1: Update tests first (rename + new coverage)**

In `backend/tests/test_providers.py`:

a. Replace the import line

```python
from app.core.llm.providers.openai import OpenAIProvider
```

with

```python
from app.core.llm.providers.openrouter import OpenRouterProvider
```

(leave the `anthropic` import alone — it is removed in Task 3), and extend the existing imports to support the new stream test (`from typing import Any` is already present):

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.llm.types import Message, MessageRole, SessionConfig, StreamResult, Tool, ToolCall, ToolResult
```

b. Replace the `openai_provider` fixture with:

```python
@pytest.fixture
def openrouter_provider() -> OpenRouterProvider:
    return OpenRouterProvider(api_key="test-key", model="test-model")
```

c. Mechanically rename across the file (the conversion-test bodies stay identical):
- `TestOpenAIConvertMessages` → `TestOpenRouterConvertMessages`
- `TestOpenAIConvertTools` → `TestOpenRouterConvertTools`
- `TestOpenAIEdgeCases` → `TestOpenRouterEdgeCases`
- every parameter `openai_provider: OpenAIProvider` → `openrouter_provider: OpenRouterProvider`
- every body reference `openai_provider.` → `openrouter_provider.`
- the section banner comment `# OpenAI Provider — ...` → `# OpenRouter Provider — ...`

d. Add two new test classes at the end of the file:

```python
# ===========================================================================
# OpenRouter Provider — client configuration
# ===========================================================================


class TestOpenRouterClientConfig:
    def test_default_base_url_and_attribution_header(self) -> None:
        provider = OpenRouterProvider(api_key="test-key", model="test-model")
        assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"
        assert provider.client.default_headers.get("X-Title") == "Cueso"
        assert provider.model == "test-model"

    def test_custom_base_url(self) -> None:
        provider = OpenRouterProvider(api_key="test-key", model="m", base_url="http://localhost:11434/v1")
        assert str(provider.client.base_url) == "http://localhost:11434/v1/"


# ===========================================================================
# OpenRouter Provider — streaming robustness
# ===========================================================================


def _chunk(choices: list[Any]) -> Any:
    return SimpleNamespace(choices=choices)


def _choice(content: str | None, finish_reason: str | None) -> Any:
    return SimpleNamespace(
        delta=SimpleNamespace(content=content, tool_calls=None),
        finish_reason=finish_reason,
    )


class TestOpenRouterStreamGuard:
    @pytest.mark.asyncio
    async def test_empty_choices_chunk_skipped(self, openrouter_provider: OpenRouterProvider) -> None:
        """OpenRouter emits keep-alive/usage frames with an empty choices array."""
        chunks = [
            _chunk([]),  # must be skipped, not IndexError
            _chunk([_choice("hi", None)]),
            _chunk([_choice(None, "stop")]),
        ]

        async def fake_stream() -> Any:
            for c in chunks:
                yield c

        result = StreamResult()
        config = SessionConfig(system_prompt="", tools=[])
        with patch.object(
            openrouter_provider.client.chat.completions,
            "create",
            AsyncMock(return_value=fake_stream()),
        ):
            events = [
                e
                async for e in openrouter_provider.generate_stream(
                    [Message(role=MessageRole.USER, content="hello")], config, result
                )
            ]

        assert result.content == "hi"
        assert any(e["type"] == "message_complete" for e in events)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -v 2>&1 | tail -20`
Expected: collection error — `ModuleNotFoundError: No module named 'app.core.llm.providers.openrouter'`

- [ ] **Step 3: Implement the rename**

```bash
git mv app/core/llm/providers/openai.py app/core/llm/providers/openrouter.py
```

Then edit `app/core/llm/providers/openrouter.py`:

a. Module docstring: `"""OpenRouter LLM provider implementation (OpenAI-compatible gateway)."""`

b. Replace the class header and constructor:

```python
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    """OpenRouter provider — routes to any model behind OpenRouter's OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str, base_url: str = OPENROUTER_BASE_URL):
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={"X-Title": "Cueso"},
        )
        self.model = model
```

(The old signature defaulted `model="gpt-4"`; `model` is now required — config always supplies it.)

c. Update the two conversion docstrings that say "OpenAI's format" to "OpenAI-compatible format" and the generate/generate_stream docstrings from "from OpenAI" to "from OpenRouter".

d. Add the stream guard as the first statement of the `async for` loop in `generate_stream`:

```python
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
```

Everything else in the file stays untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -v 2>&1 | tail -10`
Expected: all pass (Anthropic classes still present and passing)

- [ ] **Step 5: Lint, typecheck, full suite**

Run: `uv run ruff check . && uv run pyright . && uv run pytest 2>&1 | tail -3`
Expected: clean, 205+ tests pass (nothing imports `providers.openai` eagerly — `api/chat.py` imports providers lazily inside `get_llm_provider` branches, and tests override that dependency)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(llm): rename OpenAI provider to OpenRouterProvider with base_url support"
```

---

### Task 2: Config defaults point at OpenRouter

**Files:**
- Modify: `backend/app/core/config.py:64-69` (`LLMConfig`)
- Modify: `backend/config.yml.example:20-23` (`llm:` section)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: nothing from Task 1 (pure config change)
- Produces: `settings.llm.provider == "openrouter"`, `settings.llm.model == "anthropic/claude-sonnet-4.5"`, `settings.llm.base_url == "https://openrouter.ai/api/v1"` — Task 3's `get_llm_provider` reads all three plus `settings.llm.api_key`.

- [ ] **Step 1: Update config tests first**

In `backend/tests/test_config.py`:

a. `test_defaults_load` (line 21): change

```python
            assert s.llm.provider == "anthropic"
```

to

```python
            assert s.llm.provider == "openrouter"
```

b. `test_nested_access` (lines 53-54): change

```python
            assert s.llm.provider == "anthropic"
            assert s.llm.model == "claude-3-5-sonnet-20241022"
```

to

```python
            assert s.llm.provider == "openrouter"
            assert s.llm.model == "anthropic/claude-sonnet-4.5"
            assert s.llm.base_url == "https://openrouter.ai/api/v1"
```

c. `test_llm_api_key_is_secret` (line 63): change the constructor arg `"provider": "anthropic"` to `"provider": "openrouter"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v 2>&1 | tail -10`
Expected: FAIL — `assert 'anthropic' == 'openrouter'` (and missing `base_url` attribute)

- [ ] **Step 3: Update LLMConfig**

In `backend/app/core/config.py`, replace the `LLMConfig` class with:

```python
class LLMConfig(BaseModel):
    """LLM provider settings."""

    provider: str = "openrouter"
    api_key: SecretStr | None = None
    model: str = "anthropic/claude-sonnet-4.5"
    base_url: str = "https://openrouter.ai/api/v1"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v 2>&1 | tail -5`
Expected: PASS

- [ ] **Step 5: Update config.yml.example**

Replace the `llm:` section (lines 20-23) with:

```yaml
llm:
  provider: openrouter        # OpenAI-compatible gateway (only supported value)
  api_key: your_openrouter_api_key_here
  model: anthropic/claude-sonnet-4.5   # any OpenRouter slug, e.g. z-ai/glm-5.2:nitro
  # base_url: https://openrouter.ai/api/v1   # override for other OpenAI-compatible endpoints
```

- [ ] **Step 6: Lint, typecheck, full suite**

Run: `uv run ruff check . && uv run pyright . && uv run pytest 2>&1 | tail -3`
Expected: clean, all pass

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(config): default LLM config to OpenRouter gateway"
```

---

### Task 3: Wire provider selection, delete Anthropic path, update docs

**Files:**
- Modify: `backend/app/api/chat.py:58-73` (`get_llm_provider`)
- Delete: `backend/app/core/llm/providers/anthropic.py`
- Modify: `backend/tests/test_providers.py` (drop Anthropic tests, add `get_llm_provider` tests)
- Modify: `backend/pyproject.toml` (remove `anthropic` dep, via `uv remove`)
- Modify: `CLAUDE.md` (repo root — architecture + config bullets)

**Interfaces:**
- Consumes: `OpenRouterProvider` / `OPENROUTER_BASE_URL` from Task 1; `settings.llm.{provider,api_key,model,base_url}` from Task 2
- Produces: `get_llm_provider()` returning `OpenRouterProvider` for `provider == "openrouter"`, `ValueError` otherwise. (FastAPI dependency; `test_websocket.py` continues to override it — no change there.)

- [ ] **Step 1: Write the failing dependency tests**

Add to the end of `backend/tests/test_providers.py` (add `from pydantic import SecretStr`, `from app.api.chat import get_llm_provider`, and `from app.core.config import settings` to the imports):

```python
# ===========================================================================
# get_llm_provider dependency
# ===========================================================================


class TestGetLLMProvider:
    @pytest.mark.asyncio
    async def test_returns_openrouter_provider(self) -> None:
        with (
            patch.object(settings.llm, "provider", "openrouter"),
            patch.object(settings.llm, "api_key", SecretStr("test-key")),
            patch.object(settings.llm, "model", "anthropic/claude-sonnet-4.5"),
        ):
            provider = await get_llm_provider()
            assert isinstance(provider, OpenRouterProvider)
            assert provider.model == "anthropic/claude-sonnet-4.5"
            assert str(provider.client.base_url) == "https://openrouter.ai/api/v1/"

    @pytest.mark.asyncio
    async def test_unsupported_provider_raises(self) -> None:
        with (
            patch.object(settings.llm, "provider", "anthropic"),
            patch.object(settings.llm, "api_key", SecretStr("test-key")),
        ):
            with pytest.raises(ValueError, match="Unsupported LLM provider"):
                await get_llm_provider()

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self) -> None:
        with (
            patch.object(settings.llm, "provider", "openrouter"),
            patch.object(settings.llm, "api_key", None),
        ):
            with pytest.raises(ValueError, match="API key"):
                await get_llm_provider()
```

- [ ] **Step 2: Run tests to verify the right ones fail**

Run: `uv run pytest tests/test_providers.py::TestGetLLMProvider -v 2>&1 | tail -10`
Expected: `test_returns_openrouter_provider` FAILS (`ValueError: Unsupported LLM provider: openrouter`); `test_unsupported_provider_raises` FAILS (provider `anthropic` is currently supported, so no ValueError); `test_missing_api_key_raises` PASSES already

- [ ] **Step 3: Rewrite get_llm_provider**

In `backend/app/api/chat.py`, replace the body of `get_llm_provider` (lines 58-73) with:

```python
async def get_llm_provider() -> LLMProvider:
    """Get the configured LLM provider."""
    if not settings.llm.api_key:
        raise ValueError("LLM API key is required. Set llm.api_key in config.yml (an OpenRouter key)")
    api_key = settings.llm.api_key.get_secret_value()

    if settings.llm.provider == "openrouter":
        from ..core.llm.providers.openrouter import OpenRouterProvider

        return OpenRouterProvider(api_key=api_key, model=settings.llm.model, base_url=settings.llm.base_url)

    raise ValueError(f"Unsupported LLM provider: {settings.llm.provider}")
```

- [ ] **Step 4: Delete the Anthropic provider and its tests**

```bash
git rm app/core/llm/providers/anthropic.py
```

In `backend/tests/test_providers.py` delete:
- the import `from app.core.llm.providers.anthropic import AnthropicProvider`
- the `anthropic_provider` fixture
- classes `TestAnthropicConvertMessages`, `TestAnthropicExtractSystemPrompt`, `TestAnthropicConvertTools`, `TestAnthropicEdgeCases`
- the `# Anthropic Provider — ...` banner comments

- [ ] **Step 5: Remove the anthropic dependency**

```bash
uv remove anthropic
```

Expected: `pyproject.toml` line `"anthropic>=0.8.0",` gone, `uv.lock` updated. Verify no stragglers: `grep -rn "import anthropic\|from anthropic" app tests` → no matches.

- [ ] **Step 6: Run full suite, lint, typecheck**

Run: `uv run pytest 2>&1 | tail -3 && uv run ruff check . && uv run pyright .`
Expected: all pass, clean. (Coverage of the deleted file disappears; `openrouter.py` streaming lines are now covered by the stream-guard test.)

- [ ] **Step 7: Update CLAUDE.md (repo root)**

- Line 68: change

  ```
  - `core/llm/providers/` — Concrete implementations: `anthropic.py`, `openai.py`
  ```

  to

  ```
  - `core/llm/providers/` — Concrete implementation: `openrouter.py` (OpenAI-compatible gateway; model set via `llm.model` OpenRouter slug)
  ```

- Line 91 (Configuration bullet): change the env-var example `LLM__PROVIDER=openai` to `LLM__MODEL=z-ai/glm-5.2:nitro`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(llm)!: consolidate on OpenRouter, remove direct Anthropic provider"
```

---

### Task 4: Live eval validation — Claude vs GLM 5.2 Nitro through OpenRouter

No code changes. Proves the consolidation end-to-end and produces the model comparison. **USER GATE:** requires an OpenRouter API key in the worktree's `backend/config.yml` — ask the user before starting this task.

**Files:**
- Create (gitignored, not committed): `backend/config.yml` in the worktree
- Create: `docs/superpowers/2026-08-08-openrouter-eval-comparison.md`

**Interfaces:**
- Consumes: the running dev server (`uv run python main.py` from `backend/`, port 8483); eval suite `cli/evals.sh` (defaults to `ws://localhost:8483/ws/chat`)
- Produces: committed comparison doc; recommendation on whether to flip the default model

- [ ] **Step 1: Set up worktree config (USER GATE)**

```bash
cp /code/jacob/cueso/backend/config.yml backend/config.yml
```

Then edit the `llm:` section of the worktree's `backend/config.yml`: `provider: openrouter`, `model: anthropic/claude-sonnet-4.5`, and ask the user to supply/insert their OpenRouter API key (do not echo the key into the transcript; have the user edit the file or paste it via `! $EDITOR backend/config.yml`).

- [ ] **Step 2: Confirm the GLM slug**

```bash
curl -s https://openrouter.ai/api/v1/models | uv run python -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'glm' in m['id'].lower()]"
```

Expected: a slug matching GLM 5.2 (expected `z-ai/glm-5.2`; Nitro variant is the slug + `:nitro` suffix). Record the exact slug for Step 5.

- [ ] **Step 3: Start the dev server and smoke-check**

From `backend/`: run `uv run python main.py` in the background; then poll `curl -s http://localhost:8483/health` until it returns `{"status": "healthy", ...}`.

- [ ] **Step 4: Run evals with the Claude default**

```bash
cd ../cli && uv sync && ./evals.sh
```

Record: pass/fail per eval, total, and any latency observations from the output.

- [ ] **Step 5: Rerun with GLM 5.2 Nitro**

Stop the server. Edit the worktree `backend/config.yml` `llm.model` to the confirmed GLM slug with `:nitro` suffix. Restart the server, wait for health, rerun `./evals.sh`. Record the same data.

- [ ] **Step 6: Write and commit the comparison**

Create `docs/superpowers/2026-08-08-openrouter-eval-comparison.md` with: a results table (eval × model × pass/fail), notable behavioral differences, latency notes, and a recommendation (keep Claude default vs flip to GLM). Then:

```bash
git add docs/superpowers/2026-08-08-openrouter-eval-comparison.md
git commit -m "docs: OpenRouter eval comparison (claude-sonnet-4.5 vs glm-5.2:nitro)"
```

Stop the dev server. Report the comparison to the user.
