---
name: speclib-sync
description: Generate or update code for speclib spec-library dependencies. Use when the user runs `speclib sync`, asks to generate a spec-library dependency, or after `speclib add`.
---

# speclib sync

You generate implementation code from spec-driven libraries. The `speclib` CLI
handles all bookkeeping; you do the code generation. Work **one dependency at a
time**.

## Steps

1. Run `speclib sync --plan --json` (or `speclib sync --plan --json <dep>` for a
   single dependency). It prints the work items that need generating.
2. For **each** item, in order:
   a. Read the spec files in `spec_dir` (`PROMPT.md`, `SPEC.md`, and the
      `fixtures/` tree). Follow `PROMPT.md` — it tells you how to interpret the
      spec and fixtures.
   b. Read the consumer repo's `AGENTS.md` / `CLAUDE.md` for local conventions,
      and the per-dependency `context_file` if present, to adapt the code to
      this project's shape (its types, persistence, framework).
   c. Generate the implementation into `target_path` in the item's `language`.
      If code already exists there (a resumed run), reconcile against it rather
      than starting over.
   d. Write a test in `language` that exercises the fixtures, and run it. Fix and
      repeat until it passes. If the library ships no fixtures, note that.
   e. If the agent needs a decision the spec doesn't cover (e.g. a field the
      local types lack), **ask the user** before guessing.
   f. Record provenance:
      `speclib sync --record <name> --test-command "<cmd>" --fixture-status <pass|skip>`
3. Stop after the last item. Tell the user what was generated and how to
   `speclib verify`.

Never edit `speclib.toml` or `speclib.lock` by hand — the CLI owns them.
