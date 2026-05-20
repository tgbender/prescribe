# Operating Model

This file is stable operating-model guidance for agents working in this
repository. Do not use it as task-state scratch space.

Mutable working notes, handoff summaries, local investigation logs, and
compaction-resistant task state belong in `.ai/`. That directory is intentionally
gitignored so agents can record useful local context without adding noisy or
stale scratch files to commits. Durable design decisions that should be reviewed
and versioned belong in tracked docs.

## Current Direction

Prescribe is being hardened around durable, journaled filesystem operations. The
published `safe-fs-ops` package provides reusable primitives for:

- SQLite-backed storage and migrations in `safe_fs_ops.sqlite_store`
- workspace-scoped leases, claims, and conflict coordination in
  `safe_fs_ops.workspace_state`
- operation batches, journals, checkpoints, and recovery in
  `safe_fs_ops.operation_journal`
- conservative cross-platform filesystem mutation helpers in
  `safe_fs_ops.filesystem_ops`

Do not remove existing Prescribe-local implementation code until the replacement
path has equivalent or stronger behavior and coverage.

## Workflow

- Prefer characterization tests before correctness fixes.
- Keep tests free of monkeypatching. Prefer dependency injection and explicit
  test seams.
- Make small, checkpointable changes.
- Run focused tests first, then the full suite before commits when practical.
- Use conventional commits at logical checkpoints after verification.
- Treat residual risks explicitly; do not summarize uncertain behavior as solved.
- For architecture-heavy work, preserve decisions in tracked design docs and
  current state in `.ai/`.

## Validation Policy

- Dogfood mutating behavior in isolated temp workspaces.
- Do not mutate real user configuration during validation.
- Use isolated state databases and temp `HOME`, `USERPROFILE`, and
  `XDG_CONFIG_HOME` values when testing CLI behavior that may touch user paths.
- Verify actual filesystem end state, state database records, and rollback or
  recovery behavior when working on journal/asset/rollback code.
- Keep opt-in stress tests guarded so the default suite remains fast.

## Concurrency And State

- Design new coordination code for free-threaded Python.
- Do not rely on the GIL for correctness.
- Do not share SQLite connections across threads.
- Protect mutable in-process runtime/coordinator state with real locks.
- Use SQLite transactions, lease tokens, and fencing tokens for cross-process
  correctness.
- Keep leases, claims, batches, journals, and checkpoints as separate concepts.

## Agent Strategy

- The parent thread should preserve architectural context and coordinate the
  work.
- Use bounded worker/reviewer agents only when requested by the user.
- Reviewers should prioritize correctness bugs, state-management races,
  rollback gaps, and missing characterization coverage.
- Workers should own narrow file/module scopes and avoid reverting unrelated
  user or agent changes.
