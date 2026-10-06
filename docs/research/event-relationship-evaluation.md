# Event relationship evaluation (research record)

Status: research record from 2026-10-05. Nothing here is implemented or accepted
as architecture. It preserves the evidence gathered before any event-resolution
implementation work. The motivating concept is the "Event resolution /
cross-source story linking" entry in
[future-expansions.md](../future-expansions.md).

## Purpose

To test whether Atlas's stored feed evidence (titles, summaries, timestamps,
URLs, authors, categories) is enough to judge how articles relate to underlying
real-world events, and which relationship vocabulary survives contact with real
data.

## Evidence and constraints

- All items come from the local Experiment 001 database
  (`data/experiment-001.sqlite3`, not tracked by Git) as of Run 4: 4 runs,
  177 articles, 620 sightings. Article IDs below refer to that database.
- Only stored feed evidence was used. No article pages were fetched and no web
  research or outside knowledge was used for judgments.
- The database was opened read-only throughout. The review packets themselves
  were kept outside the repository (they contain publisher text) and are not
  preserved here; this record keeps the item composition by article ID and the
  judgments.

## Pass 0: exploratory labelling (single annotator)

Claude labelled 56 deliberately informative article pairs (not random pairs)
into six relationship types plus "unrelated" and "ambiguous".

- Labels: 1 duplicate, 6 same event, 7 follow-up, 19 same entity/topic but
  different event, 14 background/analysis, 3 recurring look-alike, 2 unrelated,
  4 ambiguous.
- Title alone was sufficient for 19 pairs, title plus summary for 49; 7
  remained unresolved with stored evidence.
- 22 of 54 non-control candidates (41%) were false: shared keywords, entities
  or templates pointed at different events.

Limitation: one annotator, and the labels were later shown in a report, so they
are a provisional reference, not ground truth.

## Pass 1: blinded review of 15 pairs

Method: Claude sealed its labels for 15 of the 56 pairs (Review Pairs A–O) in
an answer key before Jeff reviewed the pairs blind, then the two were compared.

Limitation: the Pass 0 report, including these pairs' labels, had been shown
before the review, so the reviewer may not have been fully blind.

Results:

- 9 substantive agreements, 1 partial/granularity difference, 1 genuine
  disagreement, and 4 cases where both described the same relationship but the
  six-type taxonomy could not represent it.
- Agreement on "should these be merged into one event?" was 14 of 15, and on
  "are these related at all?" also 14 of 15.
- Concepts the taxonomy could not express: part-of / sub-event, event →
  reaction, pre-announcement → announcement, parent event → downstream
  developments, articles covering several events, companion documents from the
  same issuer.

Outcome: a revised three-layer model (below), proposed for testing in Pass 2.

## Model under test

| Layer | Concepts |
|---|---|
| Article ↔ article | `copy_of` |
| Article → event membership role | `reports`, `discusses`, `anticipates` |
| Event → event | `part_of`, `follows_from` (basis: `publisher_stated` or `atlas_inferred`) |
| Status of any assertion | `candidate`, `accepted`, `distinct` (optional reason: same entity, same topic, look-alike), `unresolved` |

Independent corroboration is derived (different publishers, no `copy_of`), and
source role (primary issuer vs secondary media) is a property, not a
relationship. An article may belong to several events.

## Pass 2: reviewer-first review of 25 items

### Method

- 25 items of 2–4 articles (14 of 2, 9 of 3, 2 of 4); 63 unique articles; no
  article used in more than one item; items and articles in random order.
- Each item showed only stored evidence and asked open questions about events,
  membership, roles, copies, part_of / follows_from, distinct / unresolved,
  confidence and evidence sufficiency. The proposed vocabulary was offered as
  optional; plain language was invited.
- Three items contained four pairs from Pass 1 (E, L, I, C) as stability checks.
- Jeff reviewed all items first and his judgments were locked before any
  comparison.

### Limitations

- **No blinded Claude answer key was recorded for this pass**, by design. The
  comparison uses Claude's selection-time expectations, formed while building
  the packet and before seeing Jeff's judgments but never written down, and
  reconstructed afterwards. This comparison is weaker evidence than Pass 1 and
  should not be cited as an independent inter-annotator agreement measurement.
- Claude chose the items with category targets in mind and had read most of the
  articles before.
- The reviewer had seen many of these articles, with labels, in the Pass 0
  report.
- Some target categories were thin: only one `copy_of` example and only one
  advisory with its matching announcement exist in Experiment 001, and there
  were few usable `follows_from` cases.

### Items and Jeff's judgments

Article IDs are listed in packet order. Comparison is against Claude's
reconstructed expectations.

| Item | Articles | Jeff's judgment | Comparison |
|---|---|---|---|
| 1 | 151, 150 | Same conference event; news release reports it, backgrounder provides deeper supporting detail. | Agreement |
| 2 | 59, 93 | Two Conservative policy developments; possible relationship through broader cuts, but stored evidence does not establish it. Keep relationship unresolved/distinct for now. | Agreement |
| 3 | 88, 4, 17 | Related diesel-crisis timeline: crisis → threatened export ban → pressure for reserve release → G7 reserve-release decision. Distinct events with follows-from relationships. | Agreement |
| 4 | 167, 134 | Two separate anticipated healthcare announcements; potentially related through broader federal healthcare activity, but evidence does not establish that relationship. | Agreement |
| 5 | 133, 121 | Canada-EU Milwaukee meeting and later Canada-EU summit belong to the same broader cooperation story, but are separate events; no demonstrated follows-from/part-of relationship. | Model limitation |
| 6 | 138, 158 | Two prisoner deaths are unrelated/look-alike events unless later evidence connects them. | Agreement |
| 7 | 148, 125 | Two events involving the same minister are unrelated; shared person/location/time is insufficient. | Genuine disagreement (Claude had expected a possible `part_of`; that expectation exceeded the evidence) |
| 8 | 166, 142, 147 | Three Productivity Mega Deduction articles are loosely connected by the same policy. Advisory and corresponding Brantford announcement are strongly connected; Charlottetown event is separate but part of the broader policy activity. | Partial / granularity |
| 9 | 140, 139 | Same funding announcement; news release reports it, backgrounder provides additional detail. | Agreement |
| 10 | 86, 101, 87 | Different breakdowns/takes on Andy Burnham's conference speech/plans; same underlying event with discussion/analysis roles. | Agreement |
| 11 | 110, 115 | Von der Leyen statements with North Macedonia and Montenegro appear unconnected from the stored evidence despite structural similarity/shared actor. | Partial (unresolved vs distinct) |
| 12 | 49, 8 | Two separate Gen Z/pensions feature/explainer stories; same topic, different issues/events. | Agreement |
| 13 | 113, 176 | Bulgarian farmer aid and US Midwest diesel impact are unrelated events despite shared agriculture/fuel-price context. | Agreement |
| 14 | 116, 114, 112, 111 | One broader EU-support-for-Ukraine story thread containing multiple events/documents: budget/defence support statement, €2.9bn disbursement, solidarity factsheet, and Daily News coverage. Do not assume the disbursement implements the prior statement without evidence. | Model limitation |
| 15 | 97, 98, 7 | Different articles around the US jobs market. Dedicated Guardian and BBC pieces report the same jobs-report event; Guardian live blog also contains the downstream market-reaction event. | Agreement |
| 16 | 175, 168, 170 | BT/TalkTalk articles 16.2 and 16.3 report the same rescue-deal event; 16.1 is a separate BT/customer event. (16.1 = 175, 16.2 = 168, 16.3 = 170.) | Agreement |
| 17 | 44, 12, 90, 92 | All four relate to the same UK diesel-price development; some report the event while others explain/discuss its causes or effects. | Agreement |
| 18 | 2, 27, 18 | All are AI-related but otherwise appear unrelated from the stored evidence. | Agreement |
| 19 | 100, 105 | US inflation and bond/borrowing-cost stories are separate but meaningfully economically related. Evidence is insufficient to assert a specific causal/follows-from relationship. | Partial |
| 20 | 104, 107, 26 | All relate broadly to UK economic performance. The live blog is multi-event: it overlaps the dedicated growth/income story and the separate Greggs restructuring story. The dedicated stories themselves are separate events. | Agreement |
| 21 | 156, 155 | Same FedNor funding announcement; news release reports it and backgrounder provides supporting detail. | Agreement |
| 22 | 130, 128, 135 | All describe what Canada's trade minister did at the G20 Trade Ministers' Meeting. India and France bilateral meetings are separate sub-events that are part_of the broader G20 participation. | Agreement |
| 23 | 94, 95 | Same Guardian Varoufakis podcast/content appearing at two URLs: copy_of/duplicate. | Agreement |
| 24 | 43, 25, 5 | Three separate household-energy-cost developments; broadly related through household finances/energy costs but no demonstrated same-event or follows-from relationship. | Agreement |
| 25 | 74, 85 | Podcast discusses/explains the Manchester City financial scandal/verdict; UAE investment warning is a fallout event from it. The fallout follows_from the City verdict and that relationship is publisher-stated. | Agreement |

### Aggregate result

19 agreement, 3 partial/granularity differences (items 8, 11, 19), 1 genuine
disagreement (item 7), 2 model limitations (items 5, 14). The three stability
items (15, 16, 23) matched the Pass 1 judgments.

## Findings

- **The six concepts survived:** `copy_of` (item 23, the only example),
  `reports` and `discusses` (including backgrounders, explainers, call-outs,
  podcasts, and analysis of an event no article reports), `anticipates`
  (advisories, even when Atlas never saw the announcement), `part_of` and
  `follows_from`.
- **`part_of` should be reserved for genuine sub-events** where the evidence says
  so (item 22). Shared person, place or time (item 7) and shared policy or
  campaign (item 8) were not treated as `part_of`.
- **`distinct` and `unresolved` are both needed.** The boundary between them was
  the most common partial difference (items 2, 11, 19); `distinct` was
  frequently qualified by a reason (same entity, look-alike, same topic).
- **Inferred `follows_from` needs a high bar.** Publisher-stated links were
  accepted (items 3, 15, 25); every inferred causal or "implements" link was
  declined (items 2, 14, 19).
- **Multi-event article membership is necessary.** Live blogs and round-ups
  (items 14, 15, 16, 20) report several events; without multiple membership they
  would chain unrelated events together.
- **Stored feed evidence was almost always sufficient for membership and roles.**
  Where it was not, the open question concerned a relationship between events,
  not membership (items 2, 11, 14, 19; richer evidence would help items 7, 11 and
  14).

### Naive similarity/clustering failure modes observed

- Templated text merged as one event: inmate-death notices (6), healthcare
  advisories (4), von der Leyen statements (11), identical boilerplate in policy
  releases (8).
- Shared entity or keyword merged as one event: BT (16), "Super Intelligence"
  (18), Conservatives (2), same minister (7), farmers and fuel (13), energy bills
  (24), Gen Z pensions (12).
- Live blogs and round-ups chaining separate events together (14, 20).
- High similarity implying causal links that the evidence does not support
  (14, 19).

### Possible future Story/Thread layer (out of scope)

In items 5, 8, 14, 19, 20 and 24 the reviewer grouped separate events into a
broader story, thread or topic while explicitly declining `part_of` or
`follows_from`. This suggests a looser, optional grouping layer above events
rather than another event-to-event relationship type. It is recorded as an
evidence-supported future concept and is **explicitly out of scope for the first
implementation experiment**.

## Structured gold encoding

Jeff's Pass 1 and Pass 2 judgments are preserved as structured data in
[event-relationship-gold.json](event-relationship-gold.json): article IDs, event
IDs, membership roles, event relations with their basis, and one label per
judged article pair, with `unresolved` and `not_scored` kept as-is. Pass 1's
article IDs and Jeff's Pass 1 judgments were not previously committed; they are
recorded there.

The structure is Claude's interpretation of Jeff's natural-language judgments.
Jeff reviewed it and approved it, with explicit corrections, on 2026-10-05. It
holds 68 pair labels (15 from Pass 1, 53 from Pass 2), and only Jeff's
judgments: Claude's labels are not part of it. Its limitations are listed in the
file itself.

The Run 4 database those article IDs refer to was frozen on 2026-10-06 as a
local, untracked, read-only snapshot. Its identity and validation are recorded
in [experiment-001-run4-corpus-manifest.json](experiment-001-run4-corpus-manifest.json).

## Recommended next step

A small, scratch-only baseline, using stored feed evidence only, that:

1. generates candidates from title/summary overlap, a time window and document
   categories where present;
2. proposes only `copy_of`, membership roles, and `follows_from` where the
   publisher states the link, leaving everything else `unresolved`;
3. is measured against the judged items from Passes 1 and 2, with emphasis on
   false merges among templated and shared-entity negatives, correct handling of
   multi-event articles, and appropriate use of `unresolved`.

No baseline has been implemented.
