# Experiment 002: Deterministic Event-Relationship Baseline

Status: **design frozen, not implemented.** Approved by Jeff on 2026-10-06. No
code exists, no predictions have been generated and nothing has been scored.
Every rule and threshold below is predeclared for Run 1 and must not change
before Run 1 has been predicted and scored (see "Freeze and versioning").

## Question

Can stored feed evidence alone identify useful article/event relationships,
without embeddings, LLM classification, article-page fetching or production
schema changes?

## Scope and constraints

- Standard library only. No new dependencies.
- Research code only, in `research/e002/`. Nothing in `atlas_signal/`, the
  production schema or Experiment 001 collection behavior changes.
- Input is the frozen Run 4 snapshot only (see "Corpus"). No network access.
- Outputs are untracked, under `data/experiments/002/`.
- The approved gold (`docs/research/event-relationship-gold.json`), the
  snapshot and the corpus manifest are not modified.
- Story/Thread grouping remains outside the model.
- Vocabulary is the approved one: article↔article `copy_of`; article→event
  roles `reports`, `discusses`, `anticipates`; event→event `part_of` and
  `follows_from`; pair outcomes `copy_of`, `same_event`, `linked`, `distinct`,
  `unresolved`, plus `no_candidate` for pairs never surfaced.

## Corpus

- File: `data/snapshots/experiment-001-run4.sqlite3` (local, untracked,
  read-only).
- SHA-256: `5bb7742f07dfcc495cb050414ae6c7a74b0f546e8578b9aff9c89ccfbe689dfd`.
- Identity and validation: `docs/research/experiment-001-run4-corpus-manifest.json`.
- 177 articles from five sources with articles (`cbc-business` was blocked by
  robots.txt in every run), giving 15,576 unordered article pairs.

### This is a development/evaluation set, not a held-out benchmark

The gold was produced from this same corpus, and the rules below were designed
by someone who had read the reviewed articles and the reviewer's judgments.
Several heuristics were directly influenced by that review (listed in
"Heuristics influenced by prior review"). Run 1 results therefore measure
development-set behavior only. A held-out claim needs new articles (Run 5 or
later) labelled after these rules were frozen; that is outside this experiment.

## Architecture

Two separate stages, run as separate processes:

1. **Predictor** (`research/e002/predict.py` and helpers). Reads only the
   snapshot and its own rule configuration. Writes a prediction artifact.
2. **Scorer** (`research/e002/score.py`). Reads the prediction artifact and the
   gold, and nothing else from the corpus except what the artifact records.

Within the predictor, **candidate generation** (recall-oriented, makes no
decisions) is separate from **relationship classification and event
formation** (precision-oriented). They are scored separately (Stage A and
Stage B).

### Anti-leakage requirements

- Predictor code never opens, imports, names or locates the gold file.
- Predictor code never imports scorer code.
- No article ID appears in any predictor rule, threshold, lexicon or
  tie-break. Article IDs are used only as record keys and for output ordering.
- No rule, lexicon or pattern names a specific reviewed story, entity, person,
  place, organisation or event. Lexicons contain only generic function words,
  cue phrases and publisher format markers.
- The gold is loaded only by the scorer, and only after the prediction
  artifact exists.
- Enforced by tests (see "Tests").

## Decision flow

```
snapshot (read-only, immutable; SHA-256 verified)
 -> S1  normalize text; extract numbers and entity spans
 -> S2  corpus statistics: IDF, per-source boilerplate, template skeletons
 -> S3  article typing: representative time, format class, document type,
        issuing unit, container flag
 -> S4  candidate generation (any signal) with stored signal values
 -> S5  structural pair rules, fixed precedence:
        copy_of -> template slot conflict -> companion -> advisory
        -> follows_from cue (part_of cue disabled in Run 1)
 -> S6  general same-event rule (non-container articles only)
 -> S7  constrained clustering into tentative events
 -> S8  container clause attachment (no bridging)
 -> S9  membership roles
 -> S10 relations mapped onto events
 -> S11 pair labels for all candidate pairs; write prediction artifact
 --- separate process ---
 -> S12 scorer: load gold, verify corpus hash, Stage A, Stage B,
        baselines, criteria table, diagnostics
```

## 1. Input fields

All SQLite access uses the URI `mode=ro&immutable=1`. The predictor computes
the snapshot's SHA-256 before opening it and stops if it differs from the value
above.

| Table | Fields used |
|---|---|
| `articles` | `id`, `first_source_id`, `title`, `summary`, `published_at`, `updated_at`, `date_status`, `first_seen_at`, `normalized_url` |
| `sightings` | `categories_json` of each article's latest sighting (highest `id`) |
| `sources` | `id`, `kind` |

Not used: `raw_entry_json`, `title_raw`, `summary_raw`, authors, fetch
metadata, and anything outside the snapshot. (`articles.title`/`summary` are
already HTML-to-text converted by the collector and do not vary across
sightings in this snapshot.)

Derived per article:

- **Representative time:** `published_at` if `date_status = 'ok'`, else
  `updated_at` if `date_status = 'updated_only'`, else `first_seen_at`.
  Government of Canada entries have only `updated_at`, which may be a
  modification time; this is a known limitation.
- **Publisher domain** and **URL path segments** from `normalized_url`.
- **Issuing unit:** for `canada.ca` URLs, the path segment after the language
  segment (the department). Undefined for other sources.
- **Document type:** from source categories where a source supplies a
  document-type vocabulary (Run 1 table: `news releases`, `media advisories`,
  `backgrounders`, `readouts`, `statements`). Undefined otherwise.
- **Format class:** `audio`, `video`, `opinion`, `live`, `programme` or
  `standard`, from the format markers in sections 7 and 8.

## 2. Normalization and tokenization

- Unicode NFKC, then casefold.
- Map curly quotes to `'`/`"` and en/em dashes to `-`. Remove possessive `'s`.
- Tokens: split on characters that are not letters or digits, except that
  number expressions are extracted first and kept whole.
- Stopwords: a fixed inline English list (~150 words), committed with the code.
- Stemming: plural stripping only (`-ies`→`-y`, `-es`, `-s`), applied only when
  the remaining stem has at least 4 characters.
- **Numbers:** extracted as `(currency symbol or none, value, unit)`.
  Magnitude words and suffixes (`k`, `m`, `bn`, `thousand`, `million`,
  `billion`) are expanded; comma grouping removed; percentages kept with unit
  `%`. Excluded: recognised dates, years 1900–2100 without a currency symbol,
  and bare integers below 100 without a unit or currency.
- **Distinctive number:** an extracted number that occurs in at most
  `NUM_MAX_DF` articles in the corpus.
- **Entity spans:** maximal runs of two or more capitalized tokens, or a single
  capitalized token that is not title-initial, in the original-case title;
  month and weekday names excluded. Entity spans are candidate signals and
  slot-conflict inputs only. **They are never sufficient evidence for a merge,
  relation or role.**
- **Weights:** IDF `ln(N / df)` over all 177 articles. Article vectors use
  title tokens with weight `TITLE_WEIGHT` and summary tokens with weight 1,
  after boilerplate suppression. Similarity is cosine.

## 3. Boilerplate and template suppression

Applied before same-event scoring. `copy_of` uses unsuppressed text.

- **Feed wrappers:** a leading or trailing token sequence that occurs in at
  least `WRAPPER_MIN_SHARE` of one source's summaries is stripped from that
  source's summaries.
- **Repeated sentences:** a normalized sentence of at least
  `BOILERPLATE_MIN_TOKENS` tokens, containing no distinctive number, that occurs
  in at least `BOILERPLATE_MIN_ARTICLES` articles from the same source is
  removed from all of them.
- **Source-common tokens:** a token that occurs in more than
  `SOURCE_COMMON_SHARE` of one source's articles gets weight 0 for that source.
- **Template slot conflict:** for two titles from the same source **and the
  same document type** (or the same format class where no document type
  exists): if the longest common token subsequence covers at least
  `TEMPLATE_MIN_SHARED` of the shorter title, and the non-shared parts of both
  titles each contain capitalized or numeric tokens with no overlap between
  them, the pair gets a **cannot-link** and the outcome `distinct` with reason
  `template_slot_conflict`.

## 4. Candidate generation

A pair is a candidate if the representative times are at most
`CANDIDATE_MAX_DAYS` apart **and** any of these hold:

| Signal | Rule |
|---|---|
| `lexical` | cosine ≥ `CAND_COSINE`, or within either article's top `CAND_TOP_K` neighbours with cosine ≥ `CAND_TOP_K_MIN` |
| `number` | at least one shared distinctive number |
| `entity` | a shared entity span occurring in at most `CAND_ENTITY_MAX_DF` articles |
| `tag` | a shared source category occurring in at most `CAND_TAG_MAX_DF` articles; document-type categories and catch-all categories excluded |
| `issuer` | same issuing unit within `CAND_ISSUER_HOURS` |

Run 1 catch-all categories: `POLICY_AREA=GENINFO` (whole category value,
matched after trimming and casefolding; composite values listing other codes
are not excluded).

All signal values are stored per candidate. Candidate generation never assigns
a relationship.

## 5. `copy_of`

All of the following are required:

- same publisher domain;
- same document type, or the same format class where no document type exists;
- character-trigram Jaccard of normalized titles ≥ `COPY_TITLE_JACCARD`;
- character-trigram Jaccard of normalized summaries ≥ `COPY_SUMMARY_JACCARD`;
- both summaries at least `COPY_MIN_SUMMARY_CHARS` characters;
- representative times at most `COPY_MAX_HOURS` apart;
- identical sets of distinctive numbers.

Different document types are never `copy_of`, even with identical titles.

## 6. Tentative event formation

### Structural rules (S5), fixed precedence

1. `copy_of` (section 5). Copies share an event.
2. Template slot conflict (section 3): cannot-link, `distinct`.
3. **Companion documents:** same issuing unit; representative times at most
   `COMPANION_MAX_MINUTES` apart; one is a news release and the other a
   backgrounder; cosine ≥ `COMPANION_MIN_COSINE` or a shared distinctive
   number. Outcome: same event.
4. **Advisory match:** a media advisory and a non-advisory document from the
   same issuing unit, the document's time later than the advisory's and at
   most `ADVISORY_MAX_DAYS` after it; at least `ADVISORY_MIN_ANCHORS` anchors
   from {shared distinctive number, shared rare word sequence of at least three
   non-stopword tokens occurring in at most `CAND_ENTITY_MAX_DF` articles,
   cosine ≥ `ADVISORY_MIN_COSINE`, shared entity span}, of which **at most one**
   may be an entity span. If an advisory qualifies with more than one document,
   or a document with more than one advisory, abstain (`unresolved`). Outcome:
   same event.
5. **`follows_from` cue** (section 9): records a pending relation and a
   cannot-link between the two articles.
6. **`part_of` cue: disabled in Run 1** (section 10).

### General same-event rule (S6)

For a remaining candidate pair where neither article is a container and there
is no cannot-link: representative times at most `SAME_EVENT_MAX_HOURS` apart,
**and** one of

- cosine ≥ `SAME_EVENT_COSINE`;
- a shared distinctive number and cosine ≥ `SAME_EVENT_NUMBER_COSINE`;
- title-only cosine ≥ `SAME_EVENT_TITLE_COSINE`;

**and** the shared weighted evidence includes at least one content token that
lies outside every entity span in both articles. Shared people, places or
organisations alone never qualify.

### Constrained clustering (S7)

- Edges: `copy_of`, companion, advisory and same-event decisions.
- Edges are processed in descending score (cosine; structural edges first).
  Ties are broken by the earlier representative time, then the lexically
  smaller `normalized_url`. **Article IDs are never used for tie-breaking.**
- Two clusters merge only if no cross pair has a cannot-link and every cross
  pair has cosine ≥ `CLUSTER_MIN_CROSS_COSINE`.
- Every non-container article belongs to exactly one event (a singleton if it
  joins nothing).

## 7. Containers and multi-event articles

**Container detection** uses a declared table of publisher format markers:

- a `live` URL path segment;
- a live-blog title suffix (`as it happened`, `business live`, `live`);
- a title consisting only of a date plus a generic round-up word (`daily`,
  `news`, `briefing`, `round-up`);
- programme pages (`sounds` URL path segment).

**Container attachment (S8):**

- Split the container's title into clauses at `;`, ` - ` and the
  `follows_from` cue words; split its summary into sentences.
- Each clause or sentence attaches to **at most one** event: the best-scoring
  event whose members meet either (a) a shared distinctive number plus at least
  one shared content token, or (b) cosine ≥ `CONTAINER_CLAUSE_COSINE`.
- A title clause that matches no event creates a container-only event.
- A container never creates a clustering edge and never causes two events to
  merge. Two non-container articles may share an event only through a direct
  edge between non-containers. This is the no-bridging invariant.

Multi-event membership is representable: a container may be a member of
several events. In the approved gold, articles **97 and 104** are explicitly
encoded with accepted memberships in two events each. Article 170 has further
unnamed events recorded only as a note. Article 111 participates in a container
example but is encoded with a single accepted membership, so it is not a
gold-encoded multi-event article.

## 8. Membership roles

One role per membership, from the approved three only:

- `anticipates`: document type `media advisories`, or a title matching a
  generic future-announcement construction (`to make an announcement`,
  `to announce`).
- `discusses`: format class `audio`, `video`, `opinion` or `programme`;
  document type `backgrounders` (and `factsheet` where present); or a title
  beginning with `why`, `how` or `what` (explainer form).
- `reports`: everything else, including every container membership.

## 9. `follows_from` (`publisher_stated` only)

- Cues, searched in titles only: `after`, `following`, `in response to`,
  `in the wake of`, `prompted by`.
- The cue splits the title into a head (the new development) and an
  antecedent. If the antecedent is a time expression (a number or article
  followed by a time unit), abstain.
- The antecedent must match exactly one other event under the container-clause
  rule (section 7), and that event must have a member whose representative time
  is not later than the cue article's.
- Emitted: head event `follows_from` antecedent event, `basis:
  publisher_stated`, recording the cue and its position.
- Dropped and flagged: self-loops, antecedents that are container-only events,
  and relations whose endpoints ended up in the same event.
- Summary cues are not used in Run 1.
- `atlas_inferred` is never emitted.
- Reporting: each emitted relation is listed individually. The primary safety
  criterion is **0 verified-wrong relations**; precision over a handful of
  gold relations is not presented as meaningful.

## 10. `part_of`: disabled in Run 1

Run 1 emits no `part_of` relations (`PART_OF_ENABLED = false`).

Reason: all three gold `part_of` relations come from one meeting cluster, and
the candidate cue phrases were found while those articles were visible. There is
not enough independent evidence to evaluate a general rule.

Expected Run 1 `part_of` recall is **zero by design** and is not a failure of
the baseline. Gold `linked` pairs that depend only on `part_of` are reported as
unattainable in Run 1.

Documented for possible future work only (not part of Run 1): a
publisher-stated sub-event rule where an article's text says it happened on the
margins or sidelines of, or while attending, a named event whose name appears in
another event's member titles; no parent event would ever be created; sibling
sub-events would get no relation to each other. It may only be evaluated on
evidence labelled after the rule is fixed.

## 11. `distinct`, `unresolved` and `no_candidate`

- `distinct` requires affirmative contradictory evidence. In Run 1 the only
  such evidence is a **template slot conflict** (cannot-link).
- Time separation is never evidence of distinctness. It is a gate and a
  supporting signal only.
- A candidate pair that is not merged, linked or contradicted is `unresolved`.
- A pair never surfaced as a candidate is `no_candidate`, which is not an
  assertion.

## 12. Prediction artifact

One JSON file, `data/experiments/002/run1/predictions.json` (untracked),
byte-identical across repeated runs on the same inputs (sorted keys, sorted
records, no timestamps in the file):

- `format`: `atlas-signal/e002-predictions`, `format_version`: 1, `run`:
  `run1`;
- `corpus`: path, SHA-256, table counts;
- `config`: every threshold and lexicon in this document, and the Git commit of
  the code;
- `articles`: per article, representative time, format class, document type,
  issuing unit, container flag;
- `candidates`: per pair, all signal values;
- `events`: per event, members (`article`, `role`, `rule`) and a
  container-only flag;
- `relations`: `id`, `type`, `from`, `to`, `basis`, `rule`, cue and position;
- `pairs`: per candidate pair, the outcome, reason and rules fired.

The artifact contains no titles, summaries or other publisher text. A separate
human-readable diagnostics file may include titles and therefore stays
untracked.

## 13. Scoring

The scorer verifies that the artifact's corpus SHA-256 equals the gold's
`corpus.sha256_at_encoding` before scoring.

### Gold as scored

- 68 labels over 64 unique pairs (four pairs repeat with identical labels).
- `not_scored` pairs (4) are excluded, leaving 60 scorable unique pairs.
- **Positive pairs (31):** `copy_of` 1, `same_event` 24, `linked` 6.
- **Hard negatives (28):** `distinct` 21, and `unresolved` with
  `constraint: not_same_event` 7.
- The remaining `unresolved` pair has `alt_ok: same_event`; merging it is
  acceptable.
- Primary unit: unique pairs. Occurrence-weighted results (64 labels) are also
  reported.

### Predicted pair label

Derived by the scorer from events and relations, in order: `copy_of` edge →
`copy_of`; shared event membership → `same_event`; direct relation between
their events → `linked`; otherwise the artifact's `distinct`/`unresolved`, or
`no_candidate`. A shared parent event is not a direct relation.

### Stage A: candidate generation

- Candidate recall over the 31 positive pairs, overall and per label.
- Candidate pool size against the 15,576 corpus pairs.
- Hard-negative exposure (informational).
- Per-signal contribution, and recall with each signal removed (computed from
  stored signals).

A pair surfaced as a candidate is **not** counted as correctly classified.

### Stage B: final predictions

- Merge precision and recall. A merge is a predicted `copy_of` or
  `same_event`. A merge is correct for gold `same_event`, `copy_of`, or an
  `unresolved` pair whose `alt_ok` includes `same_event`. Same-event recall is
  merges on the 24 gold `same_event` pairs divided by 24.
- False merges, broken down by gold reason or constraint. Merges of gold
  `linked` pairs are wrong merges and count against merge precision; they are
  listed separately from the 28 hard negatives.
- Container bridging: gold bridging tests plus the corpus-wide invariant.
- `copy_of`: false positives; recall on the single gold example (not
  informative).
- Relations: each emitted relation is classified as verified correct (same
  type, direction and endpoint events as an accepted gold relation),
  verified wrong (contradicted by gold), or unverifiable (listed for manual
  audit). A match to a gold relation whose basis is `unspecified` is reported
  separately.
- `linked` recall out of 6, with the Run 1 attainable ceiling: **1 of 6**.
  Three pairs need `part_of` (disabled) and two need relations whose gold basis
  is `unspecified` (not attempted).
- Unresolved safety: for the 8 gold `unresolved` pairs, a merge on a
  constrained pair is a violation; `linked` or `distinct` is reported as
  over-assertion.
- `distinct` handling: agreement, under-commitment (`unresolved` or
  `no_candidate`), false merge or false link.
- Roles: accuracy over gold accepted memberships with a role, against the
  always-`reports` baseline (49 of 62, 79%); recovery of the gold multi-event
  memberships of articles 97 and 104.

### Comparison baselines

- **Abstain-all:** every pair `no_candidate`.
- **Naive lexical:** connected components of pairs with cosine ≥
  `SAME_EVENT_COSINE`, no suppression, no cannot-links, no container handling.

## 14. Diagnostics

Untracked, under `data/experiments/002/run1/`:

- per gold pair: signals, rules fired, blocking constraints, and whether a merge
  came from a direct edge or cluster transitivity;
- missed positives at Stage A and the nearest failing signal;
- corpus-wide: event size histogram, largest events, detected containers,
  boilerplate sentences and templates per source, every emitted relation;
- ablations: no suppression, no container handling, each structural rule
  removed;
- a threshold sensitivity sweep of ±1 step, labelled report-only. It is never
  used to choose thresholds for Run 1.

## 15. Tests

All fixtures are synthetic text written for the tests; no publisher text.

- **Text:** normalization, number extraction (dates and years excluded),
  sentence splitting, boilerplate detection, template slot conflicts.
- **Candidates:** each signal fires; top-k is deterministic.
- **Decisions:** identical titles with different document types are not
  `copy_of`; near-identical same-type documents are; an A–container–B structure
  does not merge A and B; one event per container clause; multi-event
  membership; `follows_from` requires both cue and matched antecedent; no
  `atlas_inferred` output; no `part_of` output while disabled; time separation
  alone never produces `distinct`; roles map as specified.
- **Leakage:** predictor modules do not import the scorer and contain no
  reference to the gold file; the predictor runs with the gold absent;
  permuting article IDs in a fixture database yields isomorphic predictions.
- **Scorer:** `alt_ok`, `constraint` and `not_scored` handling; de-duplication
  of repeated pairs; relation verification; bridging detection.
- **Determinism:** two predictor runs produce byte-identical artifacts.
- **Optional:** a snapshot test, skipped unless an environment variable names
  the snapshot, that checks the hash, read-only access and that no SQLite
  sidecar file is created.

## 16. Files

| Path | Contents | Tracked |
|---|---|---|
| `docs/experiments/002-event-relationship-baseline.md` | This design | yes |
| `research/e002/` | `corpus.py`, `text.py`, `candidates.py`, `decide.py`, `rules.py`, `predict.py`, `score.py` | yes (not yet created) |
| `tests/test_e002_*.py` | Tests above | yes (not yet created) |
| `data/experiments/002/` | Predictions, metrics, diagnostics | no (ignored by `data/`) |
| `docs/research/experiment-002-results.md` | Results, IDs and numbers only | yes (later) |

## Frozen Run 1 configuration

All values below are frozen. They were set before any prediction or scoring
run and have not been tuned by scoring against the gold (prior review did
influence several rules; see "Heuristics influenced by prior review").

| Name | Value | Used in |
|---|---|---|
| `TITLE_WEIGHT` | 2 | §2 |
| `NUM_MAX_DF` | 5 articles | §2 |
| `WRAPPER_MIN_SHARE` | 0.30 | §3 |
| `BOILERPLATE_MIN_TOKENS` | 12 | §3 |
| `BOILERPLATE_MIN_ARTICLES` | 2 (same source) | §3 |
| `SOURCE_COMMON_SHARE` | 0.20 | §3 |
| `TEMPLATE_MIN_SHARED` | 0.50 of the shorter title | §3 |
| `CANDIDATE_MAX_DAYS` | 14 | §4 |
| `CAND_COSINE` | 0.15 | §4 |
| `CAND_TOP_K` / `CAND_TOP_K_MIN` | 5 / 0.08 | §4 |
| `CAND_ENTITY_MAX_DF` | 5 articles | §4, §6 |
| `CAND_TAG_MAX_DF` | 8 articles | §4 |
| `CAND_ISSUER_HOURS` | 24 | §4 |
| `COPY_TITLE_JACCARD` | 0.90 | §5 |
| `COPY_SUMMARY_JACCARD` | 0.80 | §5 |
| `COPY_MIN_SUMMARY_CHARS` | 80 | §5 |
| `COPY_MAX_HOURS` | 48 | §5 |
| `COMPANION_MAX_MINUTES` | 60 | §6 |
| `COMPANION_MIN_COSINE` | 0.15 | §6 |
| `ADVISORY_MAX_DAYS` | 7 | §6 |
| `ADVISORY_MIN_ANCHORS` | 2 (at most 1 entity span) | §6 |
| `ADVISORY_MIN_COSINE` | 0.25 | §6 |
| `SAME_EVENT_MAX_HOURS` | 72 | §6 |
| `SAME_EVENT_COSINE` | 0.35 | §6, baselines |
| `SAME_EVENT_NUMBER_COSINE` | 0.15 | §6 |
| `SAME_EVENT_TITLE_COSINE` | 0.50 | §6 |
| `CLUSTER_MIN_CROSS_COSINE` | 0.15 | §6 |
| `CONTAINER_CLAUSE_COSINE` | 0.40 | §7, §9 |
| `PART_OF_ENABLED` | false | §10 |
| `FOLLOWS_FROM_BASIS` | `publisher_stated` only | §9 |
| `DISTINCT_RULES` | template slot conflict only | §11 |

Lexicons (number units, format markers, container markers, document-type map,
role cues, `follows_from` cues) are frozen as listed in sections 1, 2, 7, 8 and
9. The generic English stopword list is not reproduced here; it will be
committed in `research/e002/rules.py` before Run 1 and is frozen at that commit.
It may contain only generic function words.

## Success criteria for Run 1

Stage A (candidate generation):

1. Candidate recall ≥ 90% over the 31 positive pairs (at most 3 misses).
2. Candidate pool ≤ 5% of corpus pairs (at most 778 of 15,576).

Stage B (final predictions):

3. At most 1 false merge across the 28 hard negatives.
4. Zero container bridging (gold tests and corpus-wide invariant).
5. Zero false `copy_of`.
6. Zero verified-wrong `follows_from` relations; every emitted relation has
   basis `publisher_stated`; no `atlas_inferred`.
7. Zero merges on the 7 `not_same_event`-constrained unresolved pairs.
8. Usefulness floor: same-event recall ≥ 50% (at least 12 of 24) with merge
   precision ≥ 90%. Without this floor, abstaining on everything would pass
   criteria 3–7.

Not criteria in Run 1: `part_of` recall (disabled, expected zero), `distinct`
agreement (reported only), role accuracy (reported against the majority
baseline).

The sample sizes are small. A pass means the baseline is not obviously broken
on this development set; a failure is informative.

## Heuristics influenced by prior review

Each item below was designed or adjusted with knowledge of the reviewed
articles or the reviewer's judgments. Results from these rules are not
independent evidence. Ablations report each rule's contribution.

| Heuristic | Influence |
|---|---|
| Container markers (§7) | Derived from the formats of the gold container articles (97, 104, 111, 170). |
| Companion-document rule (§6) | Four gold `same_event` pairs are release/backgrounder companions (163/164, 150/151, 139/140, 155/156). |
| Advisory rule and "at most one entity anchor" (§6) | Shaped with gold advisory pairs 147/166 and 148/167 and negatives 134/167 and 125/148 in view. |
| Template slot conflict, same-document-type restriction (§3) | The look-alike negatives (138/158, 110/115, 134/167, 143/147) informed the rule; the same-type restriction was added because it would otherwise block 147/166. |
| `follows_from` title cues and head/antecedent split (§9) | Both gold `publisher_stated` relations have this title form (articles 74 and 97). |
| Repeated-sentence threshold of 2 (§3) | Chosen knowing two reviewed releases share a boilerplate sentence (143/147). |
| Distinctive-number exemption from boilerplate (§3) | Chosen knowing container and diesel-coverage summaries share sentences with amounts (111/114 and others). |
| Explainer-form `discusses` cue (§8) | Informed by article 12. |
| Gates of 72 hours and 14 days (§4, §6) | Chosen knowing the spread of the reviewed same-event clusters and of a months-apart distinct pair (49/8). |
| One event per container clause (§7) | Chosen knowing the live blog 170 and the distinct pair 170/175. |
| Entity spans never sufficient (§2, §6) | Reflects reviewer findings on same-entity negatives; also an approved principle. |
| `part_of` cue (§10, disabled) | Cue phrases were found in reviewed articles of one meeting cluster; disabled for this reason. |

## Freeze and versioning

- This document and the configuration table are frozen before the first
  prediction run. Run 1 uses exactly this configuration.
- Run 1 is predicted once and scored once. Its results are recorded as they
  are, including failures.
- Any rule or threshold change made after seeing Run 1 results, diagnostics or
  the sensitivity sweep is **tuning on the evaluation set**. It must be a new
  run (`run2`, …) with its own configuration version and a changelog entry
  stating what changed and why, and its results must be labelled as tuned. Run 1
  results are never replaced.
- Bug fixes that change predictions also create a new run, with the bug
  described.
- Any claim of generalisation requires evaluation on articles labelled after
  the relevant configuration was frozen.

### Pre-measurement clarifications

- 2026-10-06, after design commit `1592ca8` and before the first real Stage A
  candidate run (no candidate count, recall or other Stage A result existed):
  the Run 1 catch-all category for the `tag` signal is `POLICY_AREA=GENINFO`.
  The approved proposal named EC `GENINFO` as excluded; this design kept the
  concept but omitted the value. This is a specification clarification, not
  tuning.
