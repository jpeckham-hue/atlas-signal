"""Read-only loading of the Experiment 002 corpus (design section 1).

The snapshot's SHA-256 is verified before SQLite opens it and again after
loading. SQLite access uses ``mode=ro&immutable=1``. Only the fields approved
in the design are read.
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from urllib.parse import urlsplit

from . import rules
from .text import date_spans, normalize_text


class CorpusError(Exception):
    """The corpus cannot be used as specified by the design."""


class CorpusHashMismatch(CorpusError):
    """The corpus file is not the expected snapshot."""


@dataclass(frozen=True)
class Article:
    id: int
    source_id: str
    source_kind: str
    title: str
    summary: str
    published_at: str | None
    updated_at: str | None
    date_status: str
    first_seen_at: str
    normalized_url: str
    categories: tuple[str, ...]
    representative_time: datetime
    representative_time_field: str
    publisher_domain: str
    url_path_segments: tuple[str, ...]
    issuing_unit: str | None
    document_type: str | None
    format_class: str
    container_flag: bool


@dataclass(frozen=True)
class Corpus:
    path: Path
    sha256: str
    sources: tuple[tuple[str, str], ...]  # (source_id, kind), sorted by id
    articles: tuple[Article, ...]  # sorted by id


def file_sha256(path: str | PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def connect_readonly(path: str | PathLike[str]) -> sqlite3.Connection:
    """Open ``path`` read-only and immutable; SQLite will not write to it."""
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def parse_timestamp(value: str) -> datetime:
    """Parse the collector's ``YYYY-MM-DDTHH:MM:SSZ`` UTC format."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def representative_time(
    date_status: str,
    published_at: str | None,
    updated_at: str | None,
    first_seen_at: str,
) -> tuple[datetime, str]:
    """Representative time and the field it came from.

    ``published_at`` if date_status is 'ok', ``updated_at`` if 'updated_only',
    otherwise ``first_seen_at``. A status whose field is missing is an error,
    not a silent fallback.
    """
    if date_status == "ok":
        field, value = "published_at", published_at
    elif date_status == "updated_only":
        field, value = "updated_at", updated_at
    else:
        field, value = "first_seen_at", first_seen_at
    if value is None:
        raise CorpusError(f"date_status {date_status!r} but {field} is missing")
    return parse_timestamp(value), field


def publisher_domain(url: str) -> str:
    """Host name, casefolded, without a leading ``www.``."""
    host = (urlsplit(url).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def url_path_segments(url: str) -> tuple[str, ...]:
    """Non-empty URL path segments, casefolded."""
    return tuple(s.casefold() for s in urlsplit(url).path.split("/") if s)


def issuing_unit(url: str) -> str | None:
    """Path segment after the language segment on the issuing-unit domain."""
    if publisher_domain(url) != rules.ISSUING_UNIT_DOMAIN:
        return None
    segments = url_path_segments(url)
    if len(segments) >= 2 and segments[0] in rules.ISSUING_UNIT_LANGUAGE_SEGMENTS:
        return segments[1]
    return None


def document_type(categories: tuple[str, ...]) -> str | None:
    """The single document-type category, or None if there is none or several."""
    found = {c.strip().casefold() for c in categories} & set(rules.DOCUMENT_TYPES)
    return found.pop() if len(found) == 1 else None


def format_class(url: str, title: str | None) -> str:
    """Format class from URL path segments and live-blog title suffixes.

    Precedence follows ``rules.FORMAT_URL_SEGMENTS``. A title ending in a
    spaced dash followed by a live-blog suffix is ``live``.
    """
    segments = set(url_path_segments(url))
    for name, markers in rules.FORMAT_URL_SEGMENTS:
        if name == "live" and _has_live_suffix(title):
            return "live"
        if segments & set(markers):
            return name
    return "standard"


def _has_live_suffix(title: str | None) -> bool:
    text = normalize_text(title).rstrip(" .")
    return any(text.endswith(" - " + suffix) for suffix in rules.LIVE_TITLE_SUFFIXES)


_ROUNDUP_PHRASES = [w for w in rules.ROUNDUP_WORDS if "-" in w]
_ROUNDUP_TOKENS = frozenset(w for w in rules.ROUNDUP_WORDS if "-" not in w)


def _is_date_roundup_title(title: str | None) -> bool:
    """A title made only of recognised dates and generic round-up words."""
    text = normalize_text(title)
    spans = date_spans(text)
    if not spans:
        return False
    for start, end in reversed(spans):
        text = text[:start] + " " + text[end:]
    words = re.findall(r"[^\W_]+(?:-[^\W_]+)*", text)
    return bool(words) and all(w in _ROUNDUP_TOKENS or w in _ROUNDUP_PHRASES for w in words)


def container_flag(url: str, title: str | None) -> bool:
    """Container markers of design section 7.

    A ``live`` or ``sounds`` URL path segment, a live-blog title suffix after a
    spaced dash, or a title consisting only of a date plus round-up words.
    Independent of format-class precedence.
    """
    if set(url_path_segments(url)) & set(rules.CONTAINER_URL_SEGMENTS):
        return True
    return _has_live_suffix(title) or _is_date_roundup_title(title)


def template_scope(article: Article) -> tuple[str, str]:
    """Scope within which titles may share a template (design section 3)."""
    return (article.source_id, article.document_type or article.format_class)


def _latest_categories(conn: sqlite3.Connection) -> dict[int, tuple[str, ...]]:
    rows = conn.execute(
        """
        SELECT s.article_id, s.categories_json
        FROM sightings AS s
        JOIN (SELECT article_id, MAX(id) AS id FROM sightings GROUP BY article_id) AS latest
          ON s.id = latest.id
        """
    )
    result = {}
    for article_id, categories_json in rows:
        if categories_json is None:
            result[article_id] = ()
            continue
        value = json.loads(categories_json)
        if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
            raise CorpusError(f"article {article_id}: categories_json is not a list of strings")
        result[article_id] = tuple(value)
    return result


def load_corpus(
    path: str | PathLike[str],
    expected_sha256: str = rules.CORPUS_SHA256,
) -> Corpus:
    """Verify and load the corpus. Raises CorpusHashMismatch before opening it."""
    path = Path(path)
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise CorpusHashMismatch(f"{path}: SHA-256 {actual}, expected {expected_sha256}")

    conn = connect_readonly(path)
    try:
        sources = tuple(conn.execute("SELECT id, kind FROM sources ORDER BY id"))
        kinds = dict(sources)
        categories = _latest_categories(conn)
        rows = conn.execute(
            """
            SELECT id, first_source_id, title, summary, published_at, updated_at,
                   date_status, first_seen_at, normalized_url
            FROM articles ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()

    articles = []
    for (article_id, source_id, title, summary, published_at, updated_at,
         date_status, first_seen_at, url) in rows:
        cats = categories.get(article_id, ())
        moment, field = representative_time(date_status, published_at, updated_at, first_seen_at)
        articles.append(Article(
            id=article_id,
            source_id=source_id,
            source_kind=kinds[source_id],
            title=title or "",
            summary=summary or "",
            published_at=published_at,
            updated_at=updated_at,
            date_status=date_status,
            first_seen_at=first_seen_at,
            normalized_url=url,
            categories=cats,
            representative_time=moment,
            representative_time_field=field,
            publisher_domain=publisher_domain(url),
            url_path_segments=url_path_segments(url),
            issuing_unit=issuing_unit(url),
            document_type=document_type(cats),
            format_class=format_class(url, title),
            container_flag=container_flag(url, title),
        ))

    if file_sha256(path) != actual:
        raise CorpusHashMismatch(f"{path}: changed while it was being read")
    return Corpus(path=path, sha256=actual, sources=sources, articles=tuple(articles))
