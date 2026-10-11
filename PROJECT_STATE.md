# Atlas Signal — Project State Index

Last reviewed: 2026-10-11
Status: navigation index; not a replacement for experiment specifications, research records, frozen artifacts, or production evidence.

## Start here

Atlas Signal is an experimental general-purpose news intelligence and event-tracking project.

The repository currently has one branch, `main`, but some top-level status prose is stale relative to active research. In particular, the README still says event tracking does not exist, while Experiment 002 Stage B implementation is now pushed through S8. Likewise, the Experiment 002 document's opening status line reflects its original frozen-design checkpoint; use the document as the frozen specification and recent commits as implementation-progress evidence.

Do not rewrite frozen experimental truth to make navigation cleaner. GitHub establishes pushed/committed truth only; local/uncommitted Codex work must be reported separately.

## Branch directory

| Branch | Responsibility |
| --- | --- |
| `main` | Sole current branch; collection system, research experiments, specifications, and tests. |

## Canonical roadmap and decisions

- **Existing contributor rules:** `main:CLAUDE.md`.
- **Project overview / Experiment 001 collection behavior:** `main:README.md` and `main:docs/experiments/001-feed-collection.md`.
- **Current research specification:** `main:docs/experiments/002-event-relationship-baseline.md` — frozen Experiment 002 design and Stage A/Stage B decision flow.
- **Experiment 002 implementation:** `main:research/e002/`; verify recent commits rather than relying on the specification's original status line.
- **Future ideas, not current commitments:** `main:docs/future-expansions.md`.
- **Research evidence/manifests:** `main:docs/research/`; preserve sealed artifacts and anti-leakage boundaries.

## Current strategic priority

Continue Experiment 002 Stage B in the frozen sequence. As of the latest verified pushed commit, S5 structural rules, S6 same-event decisions, S7 constrained clustering and S8 container attachment are implemented; **S9-S11 remain unimplemented**. No real Stage B execution or gold evaluation has occurred; implementation so far is verified with synthetic tests only. Preserve the frozen design, sealed Stage A inputs, anti-leakage rules, and the separation between predictor and scorer.

## Current blockers / intentional deferrals

- S9-S11 are not implemented yet; do not report Stage B as complete.
- The S8 invariant that a container attached to the same event more than once yields one membership while retaining per-unit provenance has no dedicated regression test yet.
- Real Stage B execution/scoring must follow the experiment's frozen process and leakage constraints; implementation commits explicitly avoid treating synthetic tests as real evaluation.
- Story/Thread grouping remains outside the Experiment 002 model.
- Embeddings, LLM classification, article-page fetching, and production-schema changes are outside Experiment 002's frozen scope.
- Future-expansion ideas are parked, not active commitments.

## Latest verified milestone

`5dd4f8086d1bfc27df2e44b9ea87d299621022be` (2026-10-11) implements Experiment 002 Stage B S8 container attachment to the S7 events. The commit explicitly states that S9-S11 remain unimplemented. It added 32 synthetic tests; at that commit the focused Stage B/leakage run passed 153 tests and the full suite passed 594 tests (1 skipped), and all 25 S8 mutation checks were caught.

It follows the specification clarification `a53ceb29547b521f382857cfec5f44154ebd593f` (2026-10-07), which froze C23-C24 (S8 head clause and tie-break) before S8 implementation, and `b0ff0f01591a0804331c6ef3d2d9d08fccc92aa4` (2026-10-07), which implemented S7 constrained clustering over the S5/S6 edges.

Earlier, `eabce623b87cba6c96f28dff93b25dca8b6955e1` (2026-10-07) implemented S6 same-event decisions over sealed candidate pairs, and the specification clarification `91f3e046d3a0c513e2e525af73f37ac0968f06e5` froze C20-C22 before S6/S7 implementation.

## Agent retrieval procedure

1. Read `PROJECT_STATE.md` first.
2. Read `AGENTS.md` and `CLAUDE.md` before changing or reporting project state.
3. Follow explicit branch-qualified paths and verify files/commits directly.
4. Treat the Experiment 002 document as the frozen design; use commit history to determine which stages are actually implemented.
5. Verify relevant branch/file/commit state before making claims.
6. Distinguish pushed GitHub state from local/uncommitted Codex state.
7. Immediately report stale search, stale status prose, missing refs, access problems, or contradictory state rather than silently falling back to older information.
8. Never modify sealed/frozen research evidence merely to make documentation appear current.

## Maintenance contract

Update `PROJECT_STATE.md` in the same change set whenever the canonical roadmap/current-state source changes, its location changes, branch responsibilities change, a major priority changes, a major blocker is added/resolved, a significant milestone is formally reached, or a new branch becomes authoritative for part of the project.

Keep this file concise and link to source documents rather than copying experimental specifications. If this index becomes stale or contradicts linked sources, flag the problem and verify source truth before continuing.
