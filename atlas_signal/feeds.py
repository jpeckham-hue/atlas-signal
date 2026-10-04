"""Parse RSS/Atom feed bodies and map entries onto Atlas Signal's article fields.

Parsing only: this module never makes network requests. It takes the bytes of
an already-downloaded feed and returns normalized entries plus per-entry errors.

feedparser quirks handled here:
- ``FeedParserDict.get("updated")`` silently falls back to ``published``, so
  date fields are read with ``dict.get`` to see only what the feed contained.
- feedparser already copies an RSS guid into ``link`` when the guid is a
  permalink, and copies an Atom ``id`` into ``link`` when there is no link.
- Values from feedparser are already decoded once and HTML-sanitized; the
  "raw" values kept here are feedparser's output, not the original XML text.
"""

import calendar
import hashlib
import html
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import feedparser

from atlas_signal.db import utc_timestamp
from atlas_signal.urls import normalize_url

FUTURE_TOLERANCE = timedelta(hours=24)


@dataclass(frozen=True)
class FeedEntry:
    """One usable feed entry, normalized, with the feed's own values kept alongside."""

    index: int
    entry_guid: str | None
    url_as_seen: str
    normalized_url: str
    title_raw: str | None
    title: str | None
    summary_raw: str | None
    summary: str | None
    author_raw: str | None
    author: str | None
    categories: tuple[str, ...]
    published_raw: str | None
    published_at: str | None
    updated_raw: str | None
    updated_at: str | None
    date_status: str  # ok | updated_only | unparseable | missing
    language: str | None
    raw_entry_json: str
    entry_sha256: str


@dataclass(frozen=True)
class EntryError:
    """A feed entry that could not be turned into a FeedEntry."""

    index: int
    entry_guid: str | None
    link_raw: str | None
    error: str
    raw_entry_json: str | None


@dataclass(frozen=True)
class ParsedFeed:
    entries: tuple[FeedEntry, ...]
    errors: tuple[EntryError, ...]
    bozo: bool
    bozo_message: str | None
    version: str  # feedparser's detected format, e.g. "rss20", "atom10"; "" if unknown
    language: str | None

    @property
    def entry_count(self) -> int:
        return len(self.entries) + len(self.errors)


def parse_feed(body: bytes) -> ParsedFeed:
    """Parse a feed body. Never raises for bad feed content.

    A malformed feed still returns whatever entries feedparser recovered, with
    ``bozo`` set. An empty or unrecognised body returns no entries.
    """
    # A file-like object stops feedparser treating the input as a URL or path.
    parsed = feedparser.parse(io.BytesIO(body))
    exc = parsed.get("bozo_exception")
    bozo_message = f"{type(exc).__name__}: {exc}" if exc is not None else None
    language = _clean_language(parsed.feed.get("language"))

    entries: list[FeedEntry] = []
    errors: list[EntryError] = []
    for index, raw in enumerate(parsed.entries):
        try:
            entries.append(map_entry(raw, index, language))
        except Exception as err:  # one bad entry must not lose the rest
            errors.append(
                EntryError(
                    index=index,
                    entry_guid=_string(dict.get(raw, "id")),
                    link_raw=_string(dict.get(raw, "link")),
                    error=str(err) or type(err).__name__,
                    raw_entry_json=_safe_raw_json(raw),
                )
            )
    return ParsedFeed(
        entries=tuple(entries),
        errors=tuple(errors),
        bozo=bool(parsed.get("bozo")),
        bozo_message=bozo_message,
        version=parsed.get("version") or "",
        language=language,
    )


class EntryMappingError(ValueError):
    """An entry cannot be used, e.g. because it has no usable URL."""


def map_entry(raw: dict, index: int, language: str | None = None) -> FeedEntry:
    """Map one feedparser entry onto a FeedEntry, or raise EntryMappingError."""
    guid = _string(dict.get(raw, "id"))
    url_as_seen = _select_url(raw, guid)
    try:
        normalized = normalize_url(url_as_seen)
    except ValueError as err:
        raise EntryMappingError(f"unusable URL {url_as_seen!r}: {err}") from None

    title_raw = _string(dict.get(raw, "title"))
    summary_raw = _string(dict.get(raw, "summary"))
    author_raw = _string(dict.get(raw, "author"))

    published_raw = _string(dict.get(raw, "published"))
    updated_raw = _string(dict.get(raw, "updated"))
    published_at = _parsed_to_utc(dict.get(raw, "published_parsed")) if published_raw else None
    updated_at = _parsed_to_utc(dict.get(raw, "updated_parsed")) if updated_raw else None

    entry_language = _clean_language(
        (dict.get(raw, "title_detail") or {}).get("language")
    ) or language

    return FeedEntry(
        index=index,
        entry_guid=guid,
        url_as_seen=url_as_seen,
        normalized_url=normalized,
        title_raw=title_raw,
        title=clean_text(title_raw),
        summary_raw=summary_raw,
        summary=html_to_text(summary_raw),
        author_raw=author_raw,
        author=clean_text(author_raw),
        categories=_categories(raw),
        published_raw=published_raw,
        published_at=published_at,
        updated_raw=updated_raw,
        updated_at=updated_at,
        date_status=date_status(published_raw, published_at, updated_raw, updated_at),
        language=entry_language,
        raw_entry_json=raw_entry_json(raw),
        entry_sha256=entry_fingerprint(
            guid, url_as_seen, title_raw, summary_raw, published_raw, updated_raw
        ),
    )


def _select_url(raw: dict, guid: str | None) -> str:
    link = _string(dict.get(raw, "link"))
    if link:
        return link
    if guid and guid.strip().lower().startswith(("http://", "https://")):
        return guid
    raise EntryMappingError("entry has no link and no http(s) guid to use as its URL")


# --- dates -------------------------------------------------------------------


def date_status(
    published_raw: str | None,
    published_at: str | None,
    updated_raw: str | None,
    updated_at: str | None,
) -> str:
    """Classify an entry's dates.

    ok           published date parsed
    updated_only no published date text; updated date parsed
    unparseable  date text present (published, or updated with no published)
                 but it could not be parsed
    missing      no date text at all
    """
    if published_raw:
        return "ok" if published_at else "unparseable"
    if updated_raw:
        return "updated_only" if updated_at else "unparseable"
    return "missing"


def resolve_date_status(status: str, published_at: str | None, first_seen_at: str) -> str:
    """Return ``future`` if a parsed published date is over 24h after first_seen_at."""
    if status == "ok" and published_at is not None:
        published = _from_timestamp(published_at)
        if published - _from_timestamp(first_seen_at) > FUTURE_TOLERANCE:
            return "future"
    return status


def _parsed_to_utc(value) -> str | None:
    """Convert feedparser's UTC struct_time to our timestamp format."""
    if not value:
        return None
    try:
        seconds = calendar.timegm(value)
        return utc_timestamp(datetime.fromtimestamp(seconds, tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _from_timestamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# --- text --------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def clean_text(value: str | None) -> str | None:
    """Decode HTML entities and collapse whitespace. Tags are not stripped."""
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", html.unescape(value)).strip()
    return text or None


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style"}
    _BREAKS = {"br", "p", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "section", "article", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAKS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(value: str | None) -> str | None:
    """Convert an HTML fragment to a single line of plain text.

    Tags are dropped (block tags become spaces), script/style content is
    removed, entities are decoded and whitespace is collapsed. Image alt text
    is not kept.
    """
    if value is None:
        return None
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = _WHITESPACE.sub(" ", "".join(parser.parts)).strip()
    return text or None


# --- raw evidence --------------------------------------------------------------


def raw_entry_json(raw: dict) -> str:
    """Deterministic JSON for a feedparser entry (struct_time becomes a list)."""
    return json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def entry_fingerprint(
    guid: str | None,
    url_as_seen: str | None,
    title_raw: str | None,
    summary_raw: str | None,
    published_raw: str | None,
    updated_raw: str | None,
) -> str:
    """SHA-256 over the entry's identity and content fields, in a fixed order."""
    payload = json.dumps(
        [guid, url_as_seen, title_raw, summary_raw, published_raw, updated_raw],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_raw_json(raw) -> str | None:
    try:
        return raw_entry_json(raw)
    except Exception:
        return None


# --- small helpers -----------------------------------------------------------


def _string(value) -> str | None:
    """Return value if it is a non-empty string, else None."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _categories(raw: dict) -> tuple[str, ...]:
    terms = []
    for tag in dict.get(raw, "tags") or ():
        term = clean_text(_string(tag.get("term")) if isinstance(tag, dict) else None)
        if term and term not in terms:
            terms.append(term)
    return tuple(terms)


def _clean_language(value) -> str | None:
    value = _string(value)
    return value.strip().lower() if value else None
