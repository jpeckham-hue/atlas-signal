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

## Template for new ideas

```
## Idea name

### Concept

### Why it might matter

### Possible Atlas connection

### Open questions

### Evidence/research needed
```
