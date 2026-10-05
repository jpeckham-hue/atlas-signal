# Experiment 001: Feed Collection and Identity

Status: **in progress.** Smoke tests are done; the multi-day observation period
has not started. Nothing below is a final result unless it says so.

## Purpose

Establish whether a small, generic feed collector can reliably:

- collect heterogeneous RSS/Atom sources;
- preserve observations as sightings;
- recognize previously seen articles;
- recognize the same article appearing through multiple feeds;
- retain enough evidence to detect feed-entry changes;
- operate politely and conservatively over several days.

## Questions

1. How reliable are the six feeds over repeated manual collections?
2. How often do feeds change between runs?
3. How often are new articles discovered?
4. How much within-feed duplication occurs?
5. How much cross-feed/source overlap occurs?
6. Are GUIDs reliable enough to be useful?
7. How often does normalized-URL matching rescue identity?
8. Do fingerprints reveal edits to recurring entries?
9. How often do sources fail, block, redirect or omit useful metadata?
10. Is the current Article/Sighting model sufficient for the next experiment?

## Sources

As configured in `config/sources.toml`:

| ID | What it represents | Feed URL |
|---|---|---|
| `bbc-business` | BBC business news (publisher, UK) | `https://feeds.bbci.co.uk/news/business/rss.xml` |
| `cbc-business` | CBC business news (publisher, Canada) | `https://www.cbc.ca/webfeed/rss/rss-business` |
| `guardian-business` | Guardian business section (publisher, UK) | `https://www.theguardian.com/business/rss` |
| `guardian-economics` | Guardian economics subsection; deliberately overlaps `guardian-business` to test cross-feed identity | `https://www.theguardian.com/business/economics/rss` |
| `ec-press` | European Commission press releases (government, EU) | `https://ec.europa.eu/commission/presscorner/api/rss?language=en` |
| `gc-news` | Government of Canada news, 50 most recent (government, Canada) | `https://api.io.canada.ca/io-server/gc/news/en/v2?sort=publishedDate&orderBy=desc&pick=50&format=atom` |

## Procedure

Roughly 3–5 calendar days of manual collection. This is not a rigid schedule,
and the aim is not to maximize request volume.

- Normally run the collector about 2–3 times per day, when convenient.
- Do not deliberately run it every few minutes.
- Occasional longer gaps are useful: losing entries that scroll out of a feed's
  window between runs is one of the things being observed.
- Use the experiment database `data/experiment-001.sqlite3`, not the smoke-test
  database. (`data/` is ignored by Git.)
- Once useful history has accumulated, inspect it with `report`.
- Do not fetch article pages.
- Do not bypass robots.txt restrictions.
- Do not manually alter the database.

Commands (Windows, from the repository root):

Collect all sources:

```
.venv\Scripts\python.exe -m atlas_signal collect --db data/experiment-001.sqlite3
```

Collect one source (example: `ec-press`):

```
.venv\Scripts\python.exe -m atlas_signal collect --db data/experiment-001.sqlite3 --source ec-press
```

Report all-time:

```
.venv\Scripts\python.exe -m atlas_signal report --db data/experiment-001.sqlite3
```

Report since a UTC date (activity from 00:00 UTC on that date; example date):

```
.venv\Scripts\python.exe -m atlas_signal report --db data/experiment-001.sqlite3 --since 2026-10-06
```

## Success criteria

Observational, not numeric thresholds. By the end we should be able to say:

- which feeds are operational and reliable;
- whether identity/deduplication works on repeated real data;
- whether the normalized-URL fallback is genuinely useful;
- whether cross-feed overlap is represented correctly;
- whether entry and feed-body fingerprints provide useful change signals;
- what metadata deficiencies exist, by source;
- what failure modes actually occur;
- whether schema or model changes are justified by observed evidence.

## Initial live smoke-test observations (2026-10-04/05)

Runs used a throwaway database and the six sources in `config/sources.toml`.

- **EC first run** (`ec-press` only): 10 usable entries, 10 new articles, no errors.
- **Immediate EC second run**: 0 new articles, 10 GUID matches; feed body hash
  and every entry fingerprint (`entry_sha256`) unchanged.
- **Six-source run**: five sources collected successfully. CBC's robots.txt
  timed out, so CBC was conservatively skipped (`robots_disallowed`), as designed.
- **Guardian overlap**: Guardian Economics had 5 normalized-URL matches with
  articles first seen in Guardian Business.
- **BBC within-feed duplicates**: the BBC feed contained 3 story pairs that
  repeated the same URL under GUIDs differing only by fragment (e.g. `#0` / `#3`).
  Normalized-URL matching correctly merged each pair into one article.
- **gc-news dates**: entries supplied updated timestamps only, no published
  timestamps, so their articles have `date_status = updated_only`.
- No entry errors and no database-integrity problems (`foreign_key_check`,
  `integrity_check`) were observed.

## Known observations and limitations

### Observed behavior (smoke tests only; not yet confirmed over several days)

- The observations listed in the previous section.
- `guardian-business` redirected from `/business/rss` to `/uk/business/rss`
  on the same site.
- Feed windows seen in the six-source run: BBC 55 entries, Guardian Business
  40, Guardian Economics 20, EC 10, gc-news 50.
- `ec-press` sent no ETag or Last-Modified, so every EC fetch downloads the full
  feed. Guardian and gc-news sent ETags; none of the five sent Last-Modified.
- Metadata gaps: BBC entries had no author and no categories; EC entries had no
  author; gc-news entries had no language, and their author is the department
  name; EC categories arrive as a single raw string (e.g. `POLICY_AREA=A,B,C`).
- BBC entry URLs carry tracking parameters (`at_medium`, `at_campaign`), which
  normalization removes; no match in the smoke test depended on that removal.

### Current design limitations (schema v1 and the `report` command)

- Robots.txt results and redirect hops are not stored. A redirect is visible
  only when `final_url` differs from `request_url`; robots results appear only
  when a source is blocked.
- An unreachable robots.txt blocks that source for the run; there are no retries.
- Entries that leave a feed's window between runs are never seen. `report` does
  not list entries that disappeared from a window.
- Identity uses only same-source GUIDs and exact normalized URLs; there is no
  title or fuzzy matching. `canonical_url` is never populated (article pages are
  not fetched).
- Entry-fingerprint changes can be tracked only for entries with a GUID;
  sightings do not store a normalized URL.
- Feed-body comparisons cover successful (`ok`) fetches only; `not_modified`
  fetches have no body.
- Title, summary, author, language and date status are stored once per article
  (from its first sighting), so `report` measures their completeness only over
  articles first seen from each source.
- Entry errors have no timestamp of their own; `report --since` counts them by
  their fetch's start time. At the `--since` boundary a fetch and its sightings
  can fall on different sides of midnight.

### Possible future improvements (not requirements)

Candidates to evaluate against the evidence from this experiment:

- recording robots.txt and redirect outcomes per fetch;
- a retry policy for transient robots.txt or network failures;
- reporting entries that disappeared from a feed's window;
- storing per-sighting normalized URLs or metadata;
- splitting source-specific category formats.

## Final assessment

> **Intentionally incomplete.** To be written after the 3–5 day observation
> period. No conclusions have been drawn yet.

- Collection reliability: _pending_
- Deduplication behavior: _pending_
- Change detection: _pending_
- Source-specific quirks: _pending_
- Missed-window risk: _pending_
- Lessons for Experiment 002: _pending_
- Recommended changes (only if evidence supports them): _pending_
