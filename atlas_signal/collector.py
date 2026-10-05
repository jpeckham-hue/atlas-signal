"""One collection run: check robots, download each feed, parse, and persist.

Each source is processed in its own transaction, so a failure on one source
never undoes work already committed for earlier sources.

Article matching (Experiment A), in order:
1. an earlier sighting from the same source with the same non-empty guid;
2. an existing article with the same normalized URL (any source);
3. otherwise a new article.
"""

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from atlas_signal import __version__
from atlas_signal.config import SourceConfig
from atlas_signal.db import utc_timestamp
from atlas_signal.download import Downloader, RobotsPolicy, fetch_url, follow_redirects
from atlas_signal.feeds import FeedEntry, ParsedFeed, parse_feed, resolve_date_status

SAME_HOST_PAUSE_SECONDS = 1.0

# Fetch outcomes that count as success for the run status.
SUCCESS_OUTCOMES = ("ok", "not_modified")


@dataclass(frozen=True)
class SourceOutcome:
    source_id: str
    outcome: str
    http_status: int | None
    entry_count: int | None
    new_article_count: int
    sighting_count: int
    entry_error_count: int
    bozo: bool
    message: str | None


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    status: str
    started_at: str
    finished_at: str
    sources: tuple[SourceOutcome, ...]

    @property
    def exit_code(self) -> int:
        return {"completed": 0, "completed_with_errors": 1}.get(self.status, 2)


def run_status(outcomes: Iterable[str]) -> str:
    """Run status from fetch outcomes: any non-success outcome means errors."""
    return (
        "completed"
        if all(outcome in SUCCESS_OUTCOMES for outcome in outcomes)
        else "completed_with_errors"
    )


class _HostPacer:
    """Waits between consecutive requests to the same host within one run."""

    def __init__(self, sleep: Callable[[float], None], pause: float):
        self._sleep = sleep
        self._pause = pause
        self._seen: set[str] = set()

    def before_request(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        if host in self._seen:
            self._sleep(self._pause)
        self._seen.add(host)


def run_collection(
    conn: sqlite3.Connection,
    sources: Iterable[SourceConfig],
    *,
    downloader: Downloader = fetch_url,
    now: Callable[[], str] = utc_timestamp,
    sleep: Callable[[float], None] = time.sleep,
    pause: float = SAME_HOST_PAUSE_SECONDS,
) -> RunSummary:
    """Collect from ``sources`` (assumed already synced to the sources table).

    Creates one runs row, one fetches row per source, and articles/sightings/
    entry_errors as appropriate. If the run cannot continue at all, the run is
    marked failed and the exception is re-raised.
    """
    if conn.in_transaction:
        raise RuntimeError("run_collection needs a connection with no open transaction")
    sources = tuple(sources)
    started_at = now()
    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (started_at, status, app_version) VALUES (?, 'running', ?)",
            (started_at, __version__),
        ).lastrowid

    pacer = _HostPacer(sleep, pause)
    robots = RobotsPolicy(downloader, before_request=pacer.before_request)
    outcomes: list[SourceOutcome] = []
    try:
        for source in sources:
            outcomes.append(_collect_source(conn, run_id, source, downloader, robots, pacer, now))
    except BaseException as err:
        if conn.in_transaction:
            conn.rollback()
        with conn:
            conn.execute(
                "UPDATE runs SET status = 'failed', finished_at = ?, error_message = ? WHERE id = ?",
                (now(), f"{type(err).__name__}: {err}", run_id),
            )
        raise

    status = run_status(o.outcome for o in outcomes)
    finished_at = now()
    with conn:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, finished_at, run_id),
        )
    return RunSummary(run_id, status, started_at, finished_at, tuple(outcomes))


def _collect_source(conn, run_id, source, downloader, robots, pacer, now) -> SourceOutcome:
    fetch = {
        "run_id": run_id,
        "source_id": source.id,
        "started_at": now(),
        "request_url": source.feed_url,
        "bozo": 0,
    }
    try:
        return _collect_source_steps(conn, fetch, source, downloader, robots, pacer, now)
    except Exception as err:
        # Unexpected failure (a bug or database error) for this source only. Its
        # partial work was rolled back. The schema has no outcome for internal
        # failures, so it is recorded as parse_error with an explicit message.
        if conn.in_transaction:
            conn.rollback()
        failed = dict(
            fetch, outcome="parse_error", etag=None, last_modified=None,
            error_message=f"internal error: {type(err).__name__}: {err}",
        )
        return _record_fetch_only(conn, failed, now)


def _collect_source_steps(conn, fetch, source, downloader, robots, pacer, now) -> SourceOutcome:

    decision = robots.check(source.feed_url)
    if not decision.allowed:
        fetch.update(outcome="robots_disallowed", error_message=decision.reason)
        return _record_fetch_only(conn, fetch, now)

    validators = _previous_validators(conn, source.id)
    request_headers = {}
    if validators.get("etag"):
        request_headers["If-None-Match"] = validators["etag"]
    if validators.get("last_modified"):
        request_headers["If-Modified-Since"] = validators["last_modified"]

    result = follow_redirects(
        source.feed_url, request_headers, downloader,
        before_request=pacer.before_request, robots=robots,
    )
    fetch.update(final_url=result.final_url, http_status=result.status)

    if result.error:
        fetch.update(outcome=result.error, error_message=result.error_message)
        return _record_fetch_only(conn, fetch, now)

    if result.status == 304:
        fetch.update(
            outcome="not_modified",
            etag=result.headers.get("etag") or validators.get("etag"),
            last_modified=result.headers.get("last-modified") or validators.get("last_modified"),
        )
        return _record_fetch_only(conn, fetch, now)

    if result.status is None or not 200 <= result.status < 300:
        fetch.update(outcome="http_error", error_message=f"HTTP {result.status}")
        return _record_fetch_only(conn, fetch, now)

    body = result.body or b""
    fetch.update(body_sha256=hashlib.sha256(body).hexdigest(), body_bytes=len(body))
    parsed = parse_feed(body)
    fetch.update(bozo=int(parsed.bozo), bozo_message=parsed.bozo_message,
                 entry_count=parsed.entry_count)

    if parsed.entry_count == 0 and (not parsed.version or parsed.bozo):
        fetch.update(
            outcome="parse_error",
            error_message=parsed.bozo_message or "response is not a recognisable RSS/Atom feed",
        )
        return _record_fetch_only(conn, fetch, now)

    fetch.update(
        outcome="ok",
        etag=result.headers.get("etag"),
        last_modified=result.headers.get("last-modified"),
    )
    with conn:  # one transaction for the fetch row and everything it found
        fetch_id = _insert_fetch(conn, fetch)
        seen_at = now()
        new_articles = _store_entries(conn, fetch_id, source.id, parsed, seen_at)
        conn.execute(
            "UPDATE fetches SET finished_at = ?, new_article_count = ?, entry_error_count = ?"
            " WHERE id = ?",
            (now(), new_articles, len(parsed.errors), fetch_id),
        )

    return SourceOutcome(
        source_id=source.id,
        outcome="ok",
        http_status=result.status,
        entry_count=parsed.entry_count,
        new_article_count=new_articles,
        sighting_count=len(parsed.entries),
        entry_error_count=len(parsed.errors),
        bozo=parsed.bozo,
        message=parsed.bozo_message,
    )


def _record_fetch_only(conn, fetch, now) -> SourceOutcome:
    fetch = dict(fetch, finished_at=now())
    with conn:
        _insert_fetch(conn, fetch)
    return SourceOutcome(
        source_id=fetch["source_id"],
        outcome=fetch["outcome"],
        http_status=fetch.get("http_status"),
        entry_count=fetch.get("entry_count"),
        new_article_count=0,
        sighting_count=0,
        entry_error_count=0,
        bozo=bool(fetch.get("bozo")),
        message=fetch.get("error_message"),
    )


_FETCH_COLUMNS = (
    "run_id", "source_id", "started_at", "finished_at", "request_url", "final_url",
    "http_status", "outcome", "error_message", "etag", "last_modified", "body_sha256",
    "body_bytes", "bozo", "bozo_message", "entry_count", "new_article_count",
    "entry_error_count",
)


def _insert_fetch(conn, fetch) -> int:
    columns = [c for c in _FETCH_COLUMNS if c in fetch]
    placeholders = ", ".join("?" for _ in columns)
    return conn.execute(
        f"INSERT INTO fetches ({', '.join(columns)}) VALUES ({placeholders})",
        [fetch[c] for c in columns],
    ).lastrowid


def _previous_validators(conn, source_id) -> dict[str, str]:
    """ETag/Last-Modified from the source's latest successful fetch, if any."""
    row = conn.execute(
        "SELECT etag, last_modified FROM fetches"
        " WHERE source_id = ? AND outcome IN ('ok', 'not_modified')"
        " ORDER BY id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    if row is None:
        return {}
    return {k: v for k, v in (("etag", row[0]), ("last_modified", row[1])) if v}


def _store_entries(conn, fetch_id, source_id, parsed: ParsedFeed, seen_at) -> int:
    new_articles = 0
    for entry in parsed.entries:
        article_id, method = _match_article(conn, source_id, entry)
        if article_id is None:
            article_id = _insert_article(conn, source_id, entry, seen_at)
            method = "new"
            new_articles += 1
        else:
            conn.execute(
                "UPDATE articles SET last_seen_at = MAX(last_seen_at, ?) WHERE id = ?",
                (seen_at, article_id),
            )
        _insert_sighting(conn, article_id, fetch_id, source_id, entry, seen_at, method)

    for error in parsed.errors:
        conn.execute(
            "INSERT INTO entry_errors (fetch_id, entry_index, entry_guid, link_raw, error,"
            " raw_entry_json) VALUES (?, ?, ?, ?, ?, ?)",
            (fetch_id, error.index, error.entry_guid, error.link_raw, error.error,
             error.raw_entry_json),
        )
    return new_articles


def _match_article(conn, source_id, entry: FeedEntry) -> tuple[int | None, str | None]:
    if entry.entry_guid:
        row = conn.execute(
            "SELECT article_id FROM sightings WHERE source_id = ? AND entry_guid = ?"
            " ORDER BY id DESC LIMIT 1",
            (source_id, entry.entry_guid),
        ).fetchone()
        if row:
            return row[0], "guid"
    row = conn.execute(
        "SELECT id FROM articles WHERE normalized_url = ?", (entry.normalized_url,)
    ).fetchone()
    if row:
        return row[0], "normalized_url"
    return None, None


def _insert_article(conn, source_id, entry: FeedEntry, seen_at) -> int:
    return conn.execute(
        "INSERT INTO articles (normalized_url, canonical_url, first_source_id, title, summary,"
        " author, language, published_at, updated_at, date_status, first_seen_at, last_seen_at)"
        " VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry.normalized_url,
            source_id,
            entry.title,
            entry.summary,
            entry.author,
            entry.language,
            entry.published_at,
            entry.updated_at,
            resolve_date_status(entry.date_status, entry.published_at, seen_at),
            seen_at,
            seen_at,
        ),
    ).lastrowid


def _insert_sighting(conn, article_id, fetch_id, source_id, entry: FeedEntry, seen_at, method):
    conn.execute(
        "INSERT INTO sightings (article_id, fetch_id, source_id, entry_index, seen_at,"
        " match_method, entry_guid, url_as_seen, title_raw, summary_raw, author_raw,"
        " published_raw, updated_raw, categories_json, entry_sha256, raw_entry_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            article_id, fetch_id, source_id, entry.index, seen_at, method,
            entry.entry_guid, entry.url_as_seen, entry.title_raw, entry.summary_raw,
            entry.author_raw, entry.published_raw, entry.updated_raw,
            json.dumps(list(entry.categories), ensure_ascii=False),
            entry.entry_sha256, entry.raw_entry_json,
        ),
    )
