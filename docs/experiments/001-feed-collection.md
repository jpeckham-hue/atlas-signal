# Experiment 001: feed collection

Status: in progress. This file currently records only initial live smoke-test
observations; it is not the experiment's result.

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
