# Experiment 002 Stage A Run 1: diagnosis of missed pair (26, 104)

Status: post-evaluation diagnostic note, written 2026-10-06 after Stage A Run 1
was sealed. It is not part of the frozen Run 1 design and does not propose a
Run 1 change.

## Sealed Run 1 result

- Score manifest: `docs/research/experiment-002-stage-a-run1-score-manifest.json`
  (commit `af384d6`).
- Candidate artifact SHA-256: `0e319d718b35c0411c3024782d92de44900914a59e5096fef149fe0c3f3eb8a2`.
- Score artifact SHA-256: `9c0a281fea503c8ab78a302c8cb9fb9852ac2cd9befc7cf12d1b6ef1a429fe92`.
- Corpus SHA-256: `5bb7742f07dfcc495cb050414ae6c7a74b0f546e8578b9aff9c89ccfbe689dfd`.
- Candidate recall 30/31 (96.7742%), candidate pool 485/15,576 (3.1138%):
  Stage A PASS. Pair (26, 104) is the only missed positive.

## The pair

- Gold: `same_event`, item `P2-20` (Pass 2).
- Article 26 (`bbc-business`): the dedicated report of the Greggs restructuring
  event (ev51). Standard article, not a container, no source categories.
- Article 104 (`guardian-economics`): a Guardian business live blog (format
  class `live`, container flag set).
- Human gold: 104 is an intentional multi-event article that reports both ev50
  (UK growth and household income revised upwards) and ev51 (Greggs
  restructuring). 26–104 is `same_event` through ev51; 104–107 through ev50;
  26–107 is `distinct` (same topic). The broader UK economic connection is
  Story/Thread level only.

## Frozen Run 1 signals for (26, 104)

Recomputed in memory with the committed Run 1 code; the pair is absent from
the sealed candidate artifact.

| Route | Value | Frozen rule | Fired |
|---|---|---|---|
| Pair gate | 3,556 s (~0.99 h) | ≤ 14 days | inside the gate |
| `lexical_cosine` | 0.0314 | ≥ 0.15 | no |
| `lexical_top_k` | 26→104 rank 11; 104→26 rank 24; cosine 0.0314 | rank ≤ 5 and cosine ≥ 0.08 | no |
| `number` | no shared distinctive number | shared, DF ≤ 5 | no |
| `entity` | no shared eligible title entity span | shared, DF ≤ 5 | no |
| `tag` | no shared eligible tag (26 has no categories) | shared, DF ≤ 8 | no |
| `issuer` | neither article has an issuing unit | same unit within 24 h | no |

None of the six candidate routes fired.

## Evidence

- The Greggs material in 104 is one sentence of a 15-sentence stored summary.
  The rest is UK/US growth, inflation and market commentary, which dominates
  104's whole-document vector. The two vectors share only five weighted terms,
  one of which is the company name.
- 104's stored Greggs sentence concerns a trading update; the restructuring
  wording reported by 26 is not in 104's stored summary.
- 104's URL slug contains `greggs-job-cuts`, but URL text is not a Run 1
  lexical feature.
- The company name is not an eligible title entity on either side: 104's title
  does not mention it, and in 26's title it is a single title-initial token,
  which the frozen entity rule excludes.

## Failure classification

- Primary: container/multi-event dilution. A whole-document vector for a
  multi-event live blog is compared against a single-event article; the shared
  sub-event is a small part of the container's text.
- Contributing: evidence-location/lexical mismatch. The container's stored
  wording for the shared event differs from the dedicated article's, and the
  matching wording appears only in the URL slug.
- Contributing: coverage limits of the title-entity route (title spans only;
  single title-initial tokens excluded) and of the tag route (one source
  supplies no categories).

## Architectural observation

The frozen design attaches container clauses and sentences to events at
Stage B, but Stage A has no segment-level candidate route. A container
sub-event that is a small part of the container's stored text can therefore
fail to become a candidate and never reach Stage B.

## Limits of this note

- This diagnosis was made only after Run 1 was sealed.
- It is based on one development-set miss.
- It does not establish that segment-level matching, URL lexical features,
  entity-rule changes or any other modification would improve overall
  performance.
- No threshold counterfactual was performed.
- No Run 1 configuration should be changed on the basis of this note.
- Any response belongs to a separately specified future experiment.
