"""Feed source configuration: loading, validation, and syncing into the database."""

import re
import sqlite3
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from os import PathLike
from urllib.parse import urlsplit

from atlas_signal.db import utc_timestamp

SOURCE_KINDS = ("publisher", "government")

# Lowercase letters/digits in hyphen-separated groups, e.g. "guardian-business".
SOURCE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SOURCE_ID_MAX_LENGTH = 64

_REQUIRED_FIELDS = ("id", "name", "publisher", "kind", "feed_url")
_OPTIONAL_FIELDS = ("enabled",)


class ConfigError(Exception):
    """The source configuration is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    publisher: str
    kind: str
    feed_url: str
    enabled: bool = True


@dataclass(frozen=True)
class SyncResult:
    """Source ids affected by sync_sources, in the order they were processed."""

    inserted: tuple[str, ...]
    updated: tuple[str, ...]
    disabled: tuple[str, ...]
    unchanged: tuple[str, ...]


def load_sources(path: str | PathLike[str]) -> tuple[SourceConfig, ...]:
    """Read and validate a sources TOML file, preserving the file's order.

    Raises ConfigError for any problem, naming the file and the entry involved.
    """
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ConfigError(f"cannot read source config {str(path)!r}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"source config {str(path)!r} is not valid UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"source config {str(path)!r} is not valid TOML: {exc}") from exc

    try:
        return parse_sources(data)
    except ConfigError as exc:
        raise ConfigError(f"source config {str(path)!r}: {exc}") from None


def parse_sources(data: dict) -> tuple[SourceConfig, ...]:
    """Validate already-parsed TOML data (a dict with a ``sources`` array)."""
    unknown_top = set(data) - {"sources"}
    if unknown_top:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown_top))}")
    entries = data.get("sources")
    if entries is None:
        raise ConfigError("no [[sources]] entries found")
    if not isinstance(entries, list):
        raise ConfigError("'sources' must be an array of tables ([[sources]])")
    if not entries:
        raise ConfigError("no [[sources]] entries found")

    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries, start=1):
        source = _parse_entry(entry, position)
        if source.id in seen:
            raise ConfigError(f"source #{position}: duplicate id {source.id!r}")
        seen.add(source.id)
        sources.append(source)
    return tuple(sources)


def _parse_entry(entry: object, position: int) -> SourceConfig:
    where = f"source #{position}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be a table")
    if isinstance(entry.get("id"), str):
        where = f"source #{position} ({entry['id']!r})"

    unknown = set(entry) - set(_REQUIRED_FIELDS) - set(_OPTIONAL_FIELDS)
    if unknown:
        raise ConfigError(f"{where}: unknown fields: {', '.join(sorted(unknown))}")
    missing = [field for field in _REQUIRED_FIELDS if field not in entry]
    if missing:
        raise ConfigError(f"{where}: missing required fields: {', '.join(missing)}")

    for field in _REQUIRED_FIELDS:
        value = entry[field]
        if not isinstance(value, str):
            raise ConfigError(f"{where}: {field!r} must be a string, not {type(value).__name__}")
        if not value.strip():
            raise ConfigError(f"{where}: {field!r} must not be empty")
        if value != value.strip():
            raise ConfigError(f"{where}: {field!r} has leading or trailing whitespace")

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}: 'enabled' must be true or false, not {type(enabled).__name__}")

    source_id = entry["id"]
    if len(source_id) > SOURCE_ID_MAX_LENGTH or not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise ConfigError(
            f"{where}: invalid id {source_id!r}; use lowercase letters, digits and single "
            f"hyphens (max {SOURCE_ID_MAX_LENGTH} characters), e.g. 'guardian-business'"
        )

    if entry["kind"] not in SOURCE_KINDS:
        raise ConfigError(
            f"{where}: unsupported kind {entry['kind']!r}; expected one of {', '.join(SOURCE_KINDS)}"
        )

    _check_feed_url(entry["feed_url"], where)

    return SourceConfig(
        id=source_id,
        name=entry["name"],
        publisher=entry["publisher"],
        kind=entry["kind"],
        feed_url=entry["feed_url"],
        enabled=enabled,
    )


def _check_feed_url(url: str, where: str) -> None:
    """Validate a feed URL without changing it (no normalization)."""
    problem = None
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        problem = "contains whitespace or control characters"
    else:
        try:
            parts = urlsplit(url)
            parts.port  # raises ValueError for an invalid port
        except ValueError as exc:
            problem = f"is malformed ({exc})"
        else:
            if parts.scheme not in ("http", "https"):
                problem = "must be an absolute http or https URL"
            elif not parts.hostname:
                problem = "has no host"
            elif parts.username is not None or parts.password is not None:
                problem = "must not contain user credentials"
    if problem:
        raise ConfigError(f"{where}: feed_url {url!r} {problem}")


def sync_sources(
    conn: sqlite3.Connection,
    sources: Iterable[SourceConfig],
    now: str | None = None,
) -> SyncResult:
    """Make the ``sources`` table match the configuration, in one transaction.

    - New ids are inserted with first_added_at = updated_at = now.
    - Existing ids whose name, publisher, kind, feed_url or enabled flag changed
      are updated and get updated_at = now; first_added_at is never changed.
    - Rows whose id is no longer configured are set enabled = 0 (never deleted).
    - Rows that need no change are left untouched, including updated_at.

    On any error the whole sync is rolled back. ``now`` is a timestamp in the
    schema's format; it defaults to the current UTC time.
    """
    sources = tuple(sources)
    ids = [source.id for source in sources]
    if len(ids) != len(set(ids)):
        raise ConfigError("duplicate source ids passed to sync_sources")
    if now is None:
        now = utc_timestamp()

    inserted, updated, disabled, unchanged = [], [], [], []
    with conn:  # commits on success, rolls back on any exception
        existing = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT id, name, publisher, kind, feed_url, enabled FROM sources"
            )
        }
        for source in sources:
            values = (source.name, source.publisher, source.kind, source.feed_url, int(source.enabled))
            if source.id not in existing:
                conn.execute(
                    "INSERT INTO sources (id, name, publisher, kind, feed_url, enabled,"
                    " first_added_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (source.id, *values, now, now),
                )
                inserted.append(source.id)
            elif tuple(existing[source.id]) != values:
                conn.execute(
                    "UPDATE sources SET name = ?, publisher = ?, kind = ?, feed_url = ?,"
                    " enabled = ?, updated_at = ? WHERE id = ?",
                    (*values, now, source.id),
                )
                updated.append(source.id)
            else:
                unchanged.append(source.id)

        configured = set(ids)
        for source_id, row in existing.items():
            if source_id not in configured and row[-1] == 1:
                conn.execute(
                    "UPDATE sources SET enabled = 0, updated_at = ? WHERE id = ?",
                    (now, source_id),
                )
                disabled.append(source_id)

    return SyncResult(tuple(inserted), tuple(updated), tuple(disabled), tuple(unchanged))
