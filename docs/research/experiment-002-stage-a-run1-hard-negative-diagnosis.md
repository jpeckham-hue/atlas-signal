# Experiment 002 Stage A Run 1: surfaced hard-negative diagnosis

Status: post-evaluation diagnostic note (diagnosis #2), written 2026-10-06 after
Stage A Run 1 was sealed. It is not part of the frozen Run 1 design, proposes no
fix and no Run 2, and leaves Run 1 unchanged.

## Sealed Run 1 provenance

- Score manifest: `docs/research/experiment-002-stage-a-run1-score-manifest.json`
  (commit `af384d6`).
- Candidate artifact SHA-256: `0e319d718b35c0411c3024782d92de44900914a59e5096fef149fe0c3f3eb8a2`.
- Score artifact SHA-256: `9c0a281fea503c8ab78a302c8cb9fb9852ac2cd9befc7cf12d1b6ef1a429fe92`.
- Corpus SHA-256: `5bb7742f07dfcc495cb050414ae6c7a74b0f546e8578b9aff9c89ccfbe689dfd`.
- Gold SHA-256: `55e1ce1a2d9f8c59aa2c24660218aa51c32a606a5cfe5796f7f2be2890055cd2`.
- Stage A result: recall 30/31, pool 485/15,576 (3.1138%), PASS.
- Hard negatives: 28 in the gold (21 `distinct`, 7 `unresolved` with
  `not_same_event`); **23/28 entered the candidate pool**.

## Not a precision estimate

The 23/28 admission rate is **not** a precision or false-positive-rate
estimate. The gold hard negatives were deliberately chosen as confusable
evaluation cases (shared entities, templates, same topics), not sampled from all
non-positive pairs. A recall-oriented candidate finder is expected to admit many
of them. Pool size (3.1% of all pairs) is the Stage A measure of how much passes
downstream.

## Method

The 23 pairs were taken from the sealed score artifact; their routes and signal
values from the sealed candidate artifact. Hashed signal keys were resolved with
the committed feature code (in memory, no candidate generation). Article
records and gold reasoning were read only. No other candidate pairs were
examined.

## The 23 surfaced hard negatives

Routes: cos = `lexical_cosine`, topk = `lexical_top_k`, ent = `entity`,
num = `number`, iss = `issuer`. Kind: D = `distinct`, C = `unresolved` with
`not_same_event`. Gap in hours.

| Pair | Item | Kind | Routes | Gap | Cosine | Shared signal (DF) | Gold: why not same event | Why it plausibly surfaced | Category | Judgment |
|---|---|---|---|---|---|---|---|---|---|---|
| (2,18) | P2-18 | D | cos+topk | 86.9 | 0.229 | — | AI-related, otherwise unrelated | same named AI initiative vocabulary | Entity | Reasonable |
| (2,27) | P2-18 | D | cos+topk | 118.5 | 0.316 | — | as above | as above | Entity | Reasonable |
| (18,27) | P2-18 | D | cos+topk | 31.6 | 0.184 | — | as above | as above | Entity | Reasonable |
| (4,17) | P2-03 | C | cos+topk+ent | 19.7 | 0.399 | entity (3) | timeline steps; 17's membership unresolved | shared diesel/export-ban/release vocabulary | Storyline | Ambiguous |
| (4,88) | P2-03 | C | cos+topk+ent | 85.2 | 0.246 | entity (3) | as above; 88's membership unresolved | as above | Storyline | Ambiguous |
| (17,88) | P2-03 | C | cos+topk+ent | 65.4 | 0.427 | entity (3) | both memberships unresolved | both centre on the export-ban threat | Storyline | Ambiguous |
| (5,25) | P2-24 | D | cos+topk | 66.4 | 0.296 | — | separate household-energy developments | household energy bills | Topic | Reasonable |
| (5,43) | P2-24 | D | topk | 3.5 | 0.121 | — | as above | generic energy/household overlap | Topic | Noise |
| (25,43) | P2-24 | D | topk | 62.8 | 0.096 | — | as above | generic energy/household overlap | Topic | Noise |
| (14,73) | P1-M | D | topk | 47.5 | 0.118 | — | different events of one company; causal link not established | same company, two days apart | Entity | Reasonable |
| (59,93) | P2-02 | C | cos+topk+tag | 30.0 | 0.188 | 3 tags (2 each) | two party policy developments; link not established | same party, official and tags | Storyline | Reasonable |
| (100,105) | P2-19 | C | tag | 26.4 | 0.000 | 2 tags (6, 3) | separate, economically related | tags only, no lexical overlap | Storyline | Noise |
| (110,115) | P2-11 | D | cos+topk+ent | 19.9 | 0.390 | 3 entities (2 each) | separate statements despite same structure | identical statement template | Template | Reasonable |
| (113,176) | P2-13 | D | topk | 38.0 | 0.093 | — | unrelated despite shared context | generic farming/fuel/price terms | Topic | Noise |
| (121,133) | P2-05 | D | iss | 4.4 | 0.095 | issuing unit | separate events; story-level link only | same department within 24 h | Sibling | Noise |
| (130,135) | P2-22 | D | cos+topk+ent+iss | 1.9 | 0.335 | entity (4), issuing unit | sibling sub-events of one occasion | same official, occasion and readout template | Sibling | Reasonable |
| (134,167) | P2-04 | D | cos+topk+iss | 23.7 | 0.383 | issuing unit | look-alike advisories | identical advisory template | Template | Reasonable |
| (138,158) | P2-06 | D | cos+topk+iss | 21.2 | 0.517 | issuing unit | look-alike notices | identical notice template | Template | Reasonable |
| (142,147) | P2-08 | D | cos+topk+ent | 1.7 | 0.379 | entity (4) | same policy, separate promotional events | shared campaign wording | Template | Reasonable |
| (142,166) | P2-08 | D | cos+topk+ent | 22.7 | 0.289 | entity (4) | as above | as above | Template | Reasonable |
| (143,147) | P1-K | D | cos+topk+num+ent | 1.4 | 0.617 | number (2), entity (4) | same policy, different officials/scope | shared boilerplate sentence including a figure | Template | Reasonable |
| (168,175) | P1-C, P2-16 | D | cos+topk | 48.1 | 0.165 | — | different events of one company | same company, shared product vocabulary | Entity | Reasonable |
| (170,175) | P2-16 | D | cos+topk | 47.8 | 0.200 | — | as above; 170 is a multi-event live blog | as above, through the container | Entity | Reasonable |

## Taxonomy

| Primary category | Count |
|---|---|
| Same entity / named subject, different event | 6 |
| Same-issuer document family / template, different instance | 6 |
| Related storyline, relationship or sameness not established | 5 |
| Shared topic, different events | 4 |
| Sibling / adjacent events (same occasion or issuer) | 2 |

Gold kinds: 18 `distinct`, 5 constrained `unresolved` (exactly the storyline
category).

## Diagnostic judgments

| Judgment | Count |
|---|---|
| Reasonable Stage A candidate | 15 |
| Obvious Stage A noise | 5 |
| Ambiguous at the preserved-evidence level | 3 |

- Reasonable: the frozen evidence gives a plausible reason to send the pair
  downstream although the human review says it is not the same event.
- Noise: little meaningful event-level relationship, surfaced mainly through a
  weak or mechanical feature. Applied only where a single weak route admitted
  the pair and the human connection is topic- or story-level.
- Ambiguous: the gold itself leaves the relevant memberships unresolved.

These are post-hoc diagnostic judgments, **not additional gold labels**.

## Routes

| Route | Pairs |
|---|---|
| `lexical_top_k` | 21 |
| `lexical_cosine` | 17 |
| `entity` | 8 |
| `issuer` | 4 |
| `tag` | 2 |
| `number` | 1 |

Six pairs were admitted by a single route (`lexical_top_k` ×4, `tag` ×1,
`issuer` ×1). All five noise judgments are single-route admissions.

Equally, **single-route admission is not equivalent to noise**: (14,73) is a
single-route admission judged a reasonable candidate, and the sealed Run 1
score shows two true positives surfaced only by `lexical_top_k`.

## Observations

- 17/23 hard negatives passed the `lexical_cosine` route (≥ 0.15), many also
  with entity or issuer evidence. Most were not marginal threshold admissions.
- Template / document-family pairs reached cosines of about 0.29–0.62 while
  being distinct event instances.
- Stage A intentionally makes no relationship decision. No claim is made that
  Stage B would classify any of these pairs correctly.

## Parked architectural observations (not proposals)

1. Template and same-issuer document families arrive with high lexical
   similarity; separating instances is left entirely to later stages.
2. The boilerplate-suppression exemption for sentences containing a
   distinctive number let a repeated template sentence survive and also fire
   the number route (143,147).
3. Single weak routes account for all noise judgments but also for some true
   positives; one development set cannot settle that trade-off.
4. Several pairs are related only at story level, echoing the parked
   Story/Thread concept.

## Limits of this note

- This analysis occurred after Run 1 was sealed.
- `Reasonable`, `noise` and `ambiguous` are post-hoc diagnostic judgments, not
  gold labels.
- This development set cannot establish whether modifying any route would
  improve generalization.
- No threshold sweep, route ablation or counterfactual was performed.
- No Stage B evaluation occurred.
- No fix or Run 2 is proposed. Run 1 remains unchanged.
