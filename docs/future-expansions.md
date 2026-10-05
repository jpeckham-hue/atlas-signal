# Future expansions

An idea parking lot. Nothing here is a roadmap, backlog item, commitment or
architecture specification, and nothing here is implemented. Entries record
concepts and research findings so they are not lost; any of them may be
revised or discarded as evidence accumulates.

## Information propagation / event chronology

### Concept

News about an event rarely arrives as a single authoritative moment. A common
pattern is:

`first signal → corroboration → broader media pickup → official confirmation → later analysis`

Atlas may eventually need to model how information about an event spreads over
time, rather than assigning each event one timestamp.

### Why it might matter

- Markets can price information in before official confirmation; measuring a
  reaction only from the confirmation time could miss or misattribute it.
- Different stages carry different certainty: a first report may be wrong,
  partial or later contradicted.
- Many outlets repeating the same story is not the same as independent
  corroboration; syndicated or copied reports should not count as extra evidence.
- Treating the earliest observed report as the cause of a price move would be an
  unsupported causal claim unless timing and alternative explanations are
  examined.

### Possible Atlas connection

- Sightings already record when Atlas observed each article from each source;
  a chronology would sit above articles and sightings, ordering them per event.
- Market-reaction timing (see below) would need to know which stage of the
  chronology a measurement is anchored to.
- Uncertainty would likely need to be explicit for each stage, not just for the
  event as a whole.

### Open questions

- How to tell independent corroboration from syndication or re-publication.
- Whether Atlas's own observation time (`first_seen_at`) or the publisher's
  timestamp should anchor each stage, given polling delays and edited dates.
- How to represent retractions, corrections and contradicted first reports.

### Evidence/research needed

- Real examples from collected data where one event appears across sources at
  different times.
- How often publisher timestamps are missing, date-only or revised.

## Market response measurement

### Status

Research findings and a possible future direction only, from scratch
feasibility studies run on 2026-10-05 outside the Atlas codebase. None of this
is accepted design, and no market functionality exists in Atlas.

### Concept

Measuring whether and how a market responded to an event requires two
separate things: market-time semantics (when could the market first react?)
and price observations (what did prices do?). A conceptual separation worth
preserving:

`MarketClock → PriceSource → Atlas measurement/evaluation`

- **MarketClock:** exchange calendars, sessions, opening hours, holidays.
- **PriceSource:** a replaceable provider adapter that only reports what a
  provider has.
- **Atlas measurement/evaluation:** provider-neutral logic that turns an event
  time and a listing into observations.

### Findings: market time

- `exchange_calendars` 4.13.2 was tested in scratch against XNYS, XTSE, XLON
  and XPAR, including weekends, holidays, early closes and daylight-saving
  differences. It behaved correctly in all tested cases and appears to be a
  strong future candidate.
- Atlas would likely need to distinguish the publication/event time from the
  **first tradable moment** strictly after it, and define the session containing
  that moment as the **reaction session S0**.
- Possible future horizons: S0 close, S0+1 close, S0+5 close and roughly S0+21
  close (about one month), plus +1 trading hour from the first tradable moment
  where suitable intraday data exists.
- Library quirks found in testing (seconds silently floored, calendar bounds
  depending on the current date, time-zone-naive input treated as UTC) suggest
  wrapping it in a small Atlas-owned layer rather than using it directly.

### Findings: price data

- Yahoo-derived data (via `yfinance`) was adequate for personal scratch
  experiments and historical daily observations. It is not currently considered
  suitable for a serious long-term scoring system: it is an unofficial source
  intended for personal use, delisted companies disappear, and adjusted prices
  change retroactively.
- Observed Yahoo intraday retention was approximately: 1-minute data for 30
  days, 2–30-minute data for 60 days, hourly data for 730 days. Historical +1-hour
  reactions therefore cannot always be reconstructed later; live capture near
  the time of an event may be worth investigating.
- `market_prices` 0.12.14 was evaluated and should not currently be considered
  an integration candidate. Useful ideas from it: explicit availability and
  retention information, and explicit errors for unavailable data. But testing
  also found silent substitution (returning an earlier price for a later
  requested time), padded zero-volume bars on early-close days, and calendar
  assignments Atlas would not want to inherit.
- Adjusted historical prices can be revised retroactively after splits and
  dividends, so the same request can return different numbers later.

### Principles suggested by the findings

- Missing market observations should stay explicitly unavailable. Atlas should
  not interpolate, fabricate, silently substitute or quietly change resolution.
- A market observation should carry provenance, not just a number: provider,
  resolution, observation/bar time, adjustment basis, fetch time, and
  availability/staleness information.
- Daily official prices and intraday bar prices differ slightly and should not
  be mixed in one measurement.
- Provider-specific ticker/listing mapping (for example `SHOP.TO` for Shopify on
  the TSX) should be separable from Atlas entities.
- A provider adapter should make it possible to replace an experimental Yahoo
  source with a licensed one without changing event-evaluation logic.

### Open questions

- Which timestamp should represent an event: publisher time, Atlas's first
  observation, official confirmation, or a stage in a chronology (see above)?
- How to record timestamp precision and confidence (exact minute, upper bound,
  date only).
- Which exchange applies when a company has a primary listing and cross-listings.
- Whether extended-hours (pre-market/after-hours) trading should count, given
  that calendars model regular sessions only.
- Which market and sector benchmarks are appropriate for judging whether a move
  was unusual.
- Live price capture at event time versus historical retrieval later.
- Raw versus adjusted prices, and how to keep stored observations reproducible.
- What data-provider quality and licensing Atlas would eventually require.

### Evidence/research needed

- A small live-capture trial to see whether near-real-time intraday observations
  are practical within provider retention limits.
- Evaluation of at least one licensed or official price source.
- A method for abnormal-return measurement against a benchmark, tested on known
  historical events.

## Entity / security / listing resolution

### Status

Research findings and a possible future direction only, from two read-only
investigations on 2026-10-05: public identifier sources, and point-in-time
(historical) identity around renames, ticker and venue changes, acquisitions
and a spin-off. Not accepted architecture; no schema is proposed and nothing is
implemented.

### Concept

Connecting news to markets needs a chain of distinct identities:

`Mention → Entity → Security → Listing → ProviderSymbol`

- **Mention:** a textual reference in an article ("Nvidia Corp.", "NVDA") that
  needs a resolution decision.
- **Entity:** the legal/company identity.
- **Security:** a particular instrument issued by, or representing, that entity:
  a share class, ADR or CDR.
- **Listing:** that security traded on a particular venue in a particular
  currency, with validity dates.
- **ProviderSymbol:** a data provider's representation of that listing.

Testing against ten real companies showed that every one of these distinctions
is necessary.

### Examples found

- **Shopify:** one security listed in Toronto and the US; the US venue changed
  from NYSE to Nasdaq in 2025 without the ticker changing. Wikidata's listing
  information is stale after the move.
- **CN Rail:** one security, `CNR` in Toronto and `CNI` on NYSE.
- **Loblaw / Loews:** ticker `L` is Loblaw in Canada and Loews Corp in the US.
- **SAP:** the ordinary share and the ADR are different securities, although
  both trade as `SAP`.
- **Alphabet:** two share classes (`GOOGL`, `GOOG`) under one entity.
- **Meta:** the legal name changed in October 2021 but the ticker changed from
  `FB` to `META` only in June 2022. Today's OpenFIGI resolves `FB` to an
  unrelated ETF.
- **Block:** changed tickers on NYSE (`SQ` → `XYZ`) and ASX (`SQ2` → `XYZ`) on
  different days.
- **Discover and Shaw:** after acquisition and delisting, some current sources
  no longer show the old listings while others (Wikidata for Discover) still
  show them as open-ended.
- **GE:** a 2021 reverse split changed the ISIN while the ticker stayed `GE`.
  "GE Aerospace" is a brand; the legal name in the sources tested is still
  General Electric Company.
- **NVIDIA:** `NVDA.TO` is a Canadian depositary receipt, not ordinary NVIDIA
  shares.
- **Berkshire Hathaway:** one listing spelled `BRK.B`, `BRK-B` or `BRK/B`
  depending on the provider.

### Research conclusions

- Ticker symbols are not globally unique, permanent or sufficient identifiers.
- Entity, security and listing identity must not be collapsed together.
- Point-in-time identity appears feasible for experiments, but usually by
  reconstructing it from dated evidence (filings, issuer and exchange notices,
  dated source records) rather than by querying a single historical database.
- Current-state lookups can give wrong answers for historical events. An old
  ticker should never be resolved solely through a current ticker lookup.
- External identifiers (LEI, ISIN, FIGI, ticker) are evidence and attributes of
  identity, not permanent Atlas identities; an ISIN can change across a
  corporate action while the company and listing continue.
- Ticker changes and venue changes are independent of each other, and legal
  name and brand may differ.
- Every important identity relationship, not only listings and provider
  symbols, may eventually need temporal validity.
- Historical identity needs to distinguish `valid_from` / `valid_to` from
  `announced_at`, `source_recorded_at` and `fetched_at`; one change was found
  with three different dates across sources.
- Validity boundaries may be known, inferred or unknown, each with its own
  precision and confidence. Contradictory source assertions should be preserved
  rather than silently overwritten.
- Parent/subsidiary relationships (for example Google LLC under Alphabet) are
  potentially useful later but do not need to be designed now.
- No single free source tested is sufficient as Atlas's canonical identity
  backbone; several would need to be combined, each with provenance. Public
  sources appear sufficient for hand-curated experiments, but reliable
  point-in-time identity and corporate-action data at scale may eventually
  require licensed reference data.
- GLEIF (LEI) appears useful for legal entities.
- OpenFIGI appears useful for securities and listings.
- SEC EDGAR is useful for US company and lifecycle information (renames,
  delistings).
- ISO MIC codes should be preferred for Atlas's exchange identity; providers use
  their own exchange codes.
- Wikidata may be useful for aliases and cross-links but must not be treated as
  authoritative; stale and incorrect entries were found.
- FinanceDatabase may be a rough research hint, but it showed enough identifier
  errors that it should not be trusted as an authoritative source.
- Mention resolution should record its method, confidence and provenance rather
  than silently asserting identity.

### Open questions

- How to obtain point-in-time ticker and listing history.
- How to choose between primary and cross-listed securities for a given event.
- How to represent ADR, CDR and ordinary-share relationships.
- How to tell a company from a product, brand or subsidiary with a similar name.
- How to handle identifier lifecycle: lapsed, retired or duplicate identifiers.
- Which source takes precedence when authoritative sources disagree.
- What confidence thresholds should trigger human review.

### Evidence/research needed

- A mention-resolution test using real organisation names from Experiment 001.
- A cross-source identity consistency test (LEI → ISIN → FIGI → listing →
  provider symbol) for a small set of known companies.
- A point-in-time test of corporate identity and listings around known renames,
  venue moves and delistings.
- A small hand-curated "as of T" identity ledger for the historical cases above,
  testing known/inferred/unknown validity, contradictory evidence and identifier
  changes.

## Event resolution / cross-source story linking

### Status

Research findings and a possible future direction only, from a read-only
literature and code review on 2026-10-05. Not accepted architecture or an
implementation plan; nothing here is designed or implemented.

### Concept

Atlas appears to need an Event concept, but an Event should be treated as a
revisable Atlas hypothesis, not as a clustering result or an unquestioned fact.

- **Article / Sighting:** something a source published and Atlas observed.
- **Event:** Atlas's provisional model of an underlying real-world occurrence.
  Articles stay separate records; an Event links to them.

Event resolution needs to distinguish at least:

1. copied or syndicated versions of the same article;
2. different articles independently reporting the same event;
3. follow-up reporting on a developing event;
4. different events involving the same entities or topic;
5. background or analysis discussing an event;
6. recurring events that look textually similar, such as earnings reports or
   economic releases.

Syndicated repetition must not automatically count as independent
corroboration.

Motivating example: in Experiment 001 Run 4, BBC and the Guardian published
different articles about apparently the same BT/TalkTalk development about 17
minutes apart, while the same run also contained a separate BT-related story.

### Possible future direction

`new article → cheap candidate retrieval → multi-signal comparison → tentative Event relationship → optional expensive judgment for ambiguous cases`

- Useful signals from the research: URL/canonical identity, near-duplicate text,
  title/summary similarity, entity overlap, temporal proximity,
  action/predicate, numbers/amounts/periods, source and syndication
  relationships, embeddings, and selective LLM judgment. No individual signal is
  sufficient.
- Scaling: streaming resolution should compare each new article with a bounded
  candidate set, not every historical article, using temporal windows, blocking
  (for example by entity or date) and approximate retrieval.
- Revisability: later evidence may merge, split or reassign event
  relationships. Merges and splits should not silently erase earlier reasoning.
- Provenance: an Article→Event relationship should eventually be an assertion
  carrying relationship type, confidence, evidence/signals,
  method/model/version, assertion time, and revision/supersession information.

### Atlas-specific limitation

Atlas currently stores feed titles and summaries, not full article text. Most of
the event-clustering systems researched use richer text, so it is unknown how
accurately Atlas could resolve events from its deliberately thin Experiment 001
evidence.

### Reference work (not adopted)

- Topic Detection and Tracking (TDT): event versus topic definitions, first-story
  detection, story-link detection.
- Streaming first-story detection using locality-sensitive hashing.
- Priberam news clustering: streaming article-to-cluster scoring with time decay.
- Story Forest: events grouped into evolving story trees.
- USTORY: sliding-window story discovery with embeddings.
- Benchmarks: WCEP (event clusters) and SemEval-2022 Task 8 (multi-dimensional
  news similarity).

### Open questions

- How much can be resolved from title and summary alone.
- What event granularity is appropriate.
- How a tentative event becomes corroborated or confirmed.
- How to tell syndication from independent corroboration.
- How to determine event time when publisher timestamps are unstable.
- What merge, split and revision should mean.
- When embeddings or LLM judgment add enough value to justify their cost.
- Whether a later Story/Thread layer connecting related Events would be useful.

### Evidence/research needed

- Hand-label about 50 Experiment 001 article pairs into the six relationship
  types above.
- Test a cheap streaming baseline using title/summary similarity, entity overlap
  and a time window.
- Test embeddings and/or LLM judgment on ambiguous cases only, and compare
  accuracy and cost with the baseline.

## Template for new ideas

```
## Idea name

### Concept

### Why it might matter

### Possible Atlas connection

### Open questions

### Evidence/research needed
```
