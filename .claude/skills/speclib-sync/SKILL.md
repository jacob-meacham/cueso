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
      spec and fixtures. The materialized spec is always **authoritative**.
   b. Read the consumer repo's `AGENTS.md` / `CLAUDE.md` for local conventions,
      and the per-dependency `context_file` if present, to adapt the code to
      this project's shape (its types, persistence, framework).
   c. Generate the implementation into `target_path` in the item's `language`.
      - `state: "pending"` — first generation. If code already exists there (a
        resumed run), reconcile against it rather than starting over.
      - `state: "upgrade-pending"` — the spec moved from `from_commit` to
        `to_version`/`to_commit`. Read `SPEC.diff` in `spec_dir` to see exactly
        what changed, then reconcile against the **existing code already on
        disk** at `target_path`: apply the minimal change the diff implies and
        **preserve local edits**. Honor the recorded `selections` — the same
        generation choices as last time (e.g. the channel subset) — unless the
        user asks to change them. If `SPEC.diff` is empty, fall back to the full
        `SPEC.md` as the source of truth.

      **Retrofit — when this behavior already exists in hand-written code.** If
      the project already implements this behavior (a pre-existing module, or
      code on the live call path), do **not** generate a parallel copy beside
      it. Find the implementation that actually runs — trace it from the app's
      entry points, not just any file that looks related — reconcile *that* code
      against the spec, and make it the tracked `target_path`. Then delete any
      now-redundant duplicate and its tests. Two implementations of one spec
      (one tracked, one live) is the exact failure this tool exists to prevent:
      the tracked copy can pass fixtures while the live copy silently drifts.
      **The generated code must be the code that runs.**

      Put a provenance header on the generated file naming the library and the
      resolved version (e.g. `roku-deeplink v1.3.0`); on an `upgrade-pending`
      sync, update it to the new version so the file never disagrees with
      `speclib.lock`.
   d. Verify cheaply first, then thoroughly. If the plan item has a `checks`
      list, run each command in order and fix any failures until every one
      exits 0 — these are the project's declared gates (build, lint,
      format-check), and all of them must be clean before you write the
      fixture test. With no `checks`, run the project's build/compile step and
      its linter, and fix any errors they report — they're faster to catch
      than fixture failures. Either way, a check you cannot run (missing tool,
      broken command) is a step-2e situation: ask the user, never silently
      skip it. Only once those are clean, write a test in `language` that
      exercises the fixtures, and run it. Fix and repeat until the fixture
      test passes. If the library ships no fixtures, note that.
      Then confirm the code you generated is actually reachable from the app —
      imported and called outside of tests — not just exercised by the fixture
      test. A fixture test passing against code nothing calls is a false green.
   e. If the agent needs a decision the spec doesn't cover (e.g. a field the
      local types lack), **ask the user** before guessing.
   f. Record provenance:
      `speclib sync --record <name> --test-command "<cmd>" --fixture-status <pass|skip>`
      Use `--fixture-status pass` only when **every** fixture ran and passed; if
      you excluded or skipped any (e.g. a channel this consumer doesn't use),
      record `skip`, never `pass`, so the lock's status stays honest.
      Also pass `--selections "<choices>"` capturing any generation choices you
      made (e.g. which channels/options), so future upgrades honor them.
3. Stop after the last item. Tell the user what was generated and how to
   `speclib verify`, and list each check command you ran with its result.
   Never describe a sync as clean if a check failed or was skipped.

Never edit `speclib.toml` or `speclib.lock` by hand — the CLI owns them.
