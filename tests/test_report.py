"""Offline tests for the read-only report (atlas_signal.report and `report` command).

Databases are synthetic: created with the real schema in a temporary directory
and filled with direct inserts, so every expected count is known exactly.
"""

import contextlib
import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_signal import cli
from atlas_signal.db import SchemaError, open_database
from atlas_signal.report import (
    ChangeCount, ReportError, build_report, open_read_only, parse_since,
)

NOW = "2026-10-05T00:00:00Z"
DAY1, DAY2, DAY3, DAY3_LATE = (
    "2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z",
    "2026-10-03T10:00:00Z", "2026-10-03T12:00:00Z",
)


class Builder:
    """Inserts schema-v1 rows directly; ids follow insertion order."""

    def __init__(self, path):
        self.conn = open_database(path)
        for sid in ("alpha", "beta", "gamma"):
            self.conn.execute(
                "INSERT INTO sources (id, name, publisher, kind, feed_url, first_added_at,"
                " updated_at) VALUES (?, ?, ?, 'publisher', ?, ?, ?)",
                (sid, sid.title(), sid.title(), f"https://{sid}.example/rss", DAY1, DAY1),
            )
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    def insert(self, table, **values):
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values())
        ).lastrowid

    def run(self, at, status="completed"):
        return self.insert("runs", started_at=at, finished_at=at, status=status,
                           app_version="0.1.0")

    def fetch(self, run_id, source, at, outcome="ok", body=None, entries=None):
        return self.insert(
            "fetches", run_id=run_id, source_id=source, started_at=at, finished_at=at,
            request_url=f"https://{source}.example/rss", outcome=outcome,
            body_sha256=hashlib.sha256(body.encode()).hexdigest() if body else None,
            entry_count=entries,
        )

    def article(self, path, source, at, *, title="T", summary="S", author="A",
                language="en", date_status="ok"):
        return self.insert(
            "articles", normalized_url=f"https://{source}.example{path}",
            first_source_id=source, title=title, summary=summary, author=author,
            language=language, date_status=date_status, first_seen_at=at, last_seen_at=at,
        )

    def sighting(self, article_id, fetch_id, source, index, at, method, guid=None, sha="h"):
        return self.insert(
            "sightings", article_id=article_id, fetch_id=fetch_id, source_id=source,
            entry_index=index, seen_at=at, match_method=method, entry_guid=guid,
            url_as_seen="https://x.example/", entry_sha256=sha, raw_entry_json="{}",
        )

    def entry_error(self, fetch_id, index):
        return self.insert("entry_errors", fetch_id=fetch_id, entry_index=index,
                           error="no usable URL")


def build_scenario(path):
    """Four runs over three days covering every outcome and match method.

    alpha: g1 is seen in three fetches; its fingerprint changes on day 2 and
    stays the same on day 3. Feed body changes on day 2 and not on day 3.
    beta: re-sees alpha's article 1 by normalized URL on day 1 (cross-source).
    """
    b = Builder(path)
    r1 = b.run(DAY1)
    f1 = b.fetch(r1, "alpha", DAY1, body="A1", entries=3)
    a1 = b.article("/1", "alpha", DAY1)
    a2 = b.article("/2", "alpha", DAY1, summary="", author=None, language=None,
                   date_status="updated_only")
    b.sighting(a1, f1, "alpha", 0, DAY1, "new", guid="g1", sha="s1")
    b.sighting(a2, f1, "alpha", 1, DAY1, "new")
    b.entry_error(f1, 2)
    f2 = b.fetch(r1, "beta", DAY1, body="B1", entries=1)
    b.sighting(a1, f2, "beta", 0, DAY1, "normalized_url", guid="b1")

    r2 = b.run(DAY2, "completed_with_errors")
    f3 = b.fetch(r2, "alpha", DAY2, body="A2", entries=2)
    b.sighting(a1, f3, "alpha", 0, DAY2, "guid", guid="g1", sha="s1-changed")
    b.sighting(a2, f3, "alpha", 1, DAY2, "normalized_url")
    b.fetch(r2, "beta", DAY2, outcome="http_error")

    r3 = b.run(DAY3, "completed_with_errors")
    f5 = b.fetch(r3, "alpha", DAY3, body="A2", entries=2)
    b.sighting(a1, f5, "alpha", 0, DAY3, "guid", guid="g1", sha="s1-changed")
    a3 = b.article("/3", "alpha", DAY3, author=None, date_status="missing")
    b.sighting(a3, f5, "alpha", 1, DAY3, "new", guid="g3", sha="s3")
    b.fetch(r3, "beta", DAY3, outcome="not_modified")
    b.fetch(r3, "gamma", DAY3, outcome="robots_disallowed")

    r4 = b.run(DAY3_LATE, "failed")
    b.fetch(r4, "gamma", DAY3_LATE, outcome="network_error")
    b.fetch(r4, "gamma", DAY3_LATE, outcome="parse_error", body="junk", entries=0)
    b.fetch(r4, "gamma", DAY3_LATE, outcome="too_large")
    b.close()


class ParseSinceTests(unittest.TestCase):
    def test_valid_date_is_utc_midnight(self):
        self.assertEqual(parse_since("2026-10-04"), "2026-10-04T00:00:00Z")

    def test_malformed_dates_rejected(self):
        for text in ("2026-1-04", "20261004", "2026-10-04T00:00:00Z", "yesterday", "",
                     "2026-02-30", "2026-13-01", " 2026-10-04"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_since(text)


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "atlas.sqlite3"

    def report(self, since=None):
        conn = open_read_only(self.db)
        try:
            return build_report(conn, since, now=lambda: NOW)
        finally:
            conn.close()


class EmptyDatabaseTests(TempDir):
    def test_empty_valid_database(self):
        open_database(self.db).close()
        r = self.report()
        self.assertEqual((r.run_count, r.fetch_count, r.entries_observed, r.entry_errors,
                          r.articles_all_time, r.sightings, r.new_articles), (0,) * 7)
        self.assertEqual(r.runs, {})
        self.assertEqual(r.match_methods, {})
        self.assertEqual(r.fingerprint_changes, ChangeCount(0, 0))
        self.assertEqual(r.body_changes, ChangeCount(0, 0))
        self.assertEqual(r.sources, ())
        self.assertEqual(r.generated_at, NOW)


class ScenarioTests(TempDir):
    def setUp(self):
        super().setUp()
        build_scenario(self.db)

    def source(self, r, source_id):
        return {s.source_id: s for s in r.sources}[source_id]

    def test_overall_all_time(self):
        r = self.report()
        self.assertIsNone(r.since)
        self.assertEqual(r.runs, {"completed": 1, "completed_with_errors": 2, "failed": 1})
        self.assertEqual(r.run_count, 4)
        self.assertEqual(r.fetch_outcomes, {
            "ok": 4, "not_modified": 1, "http_error": 1, "network_error": 1,
            "parse_error": 1, "robots_disallowed": 1, "too_large": 1,
        })
        self.assertEqual(r.fetch_count, 10)
        self.assertEqual(r.entries_observed, 8)
        self.assertEqual(r.entry_errors, 1)
        self.assertEqual(r.articles_all_time, 3)
        self.assertEqual(r.sightings, 7)
        self.assertEqual(r.new_articles, 3)

    def test_dedup_signals_all_time(self):
        r = self.report()
        self.assertEqual(r.match_methods, {"new": 3, "guid": 2, "normalized_url": 2})
        self.assertEqual(r.cross_source_url_matches, 1)
        self.assertEqual(r.multi_source_articles, 1)
        self.assertEqual(r.multi_sighting_articles, 2)

    def test_fingerprint_changes(self):
        r = self.report()
        # g1: day 2 differs from day 1 (changed); day 3 equals day 2 (not changed).
        self.assertEqual(r.fingerprint_changes, ChangeCount(compared=2, changed=1))
        self.assertEqual(r.fingerprint_changed_guids, 1)
        self.assertEqual(self.source(r, "alpha").fingerprint_changes, ChangeCount(2, 1))
        # beta's GUID b1 was seen once: nothing to compare.
        self.assertEqual(self.source(r, "beta").fingerprint_changes, ChangeCount(0, 0))

    def test_body_changes(self):
        r = self.report()
        # alpha: A1 -> A2 changed, A2 -> A2 unchanged. beta has one successful body;
        # gamma's parse_error body is not a successful fetch.
        self.assertEqual(r.body_changes, ChangeCount(compared=2, changed=1))
        self.assertEqual(self.source(r, "alpha").body_changes, ChangeCount(2, 1))
        self.assertEqual(self.source(r, "gamma").body_changes, ChangeCount(0, 0))

    def test_per_source_all_time(self):
        r = self.report()
        self.assertEqual([s.source_id for s in r.sources], ["alpha", "beta", "gamma"])
        alpha = self.source(r, "alpha")
        self.assertEqual((alpha.fetches, alpha.successful_fetches), (3, 3))
        self.assertEqual(alpha.outcomes, {"ok": 3})
        self.assertEqual((alpha.entries_observed, alpha.entry_errors), (7, 1))
        self.assertEqual(alpha.sightings, 6)
        self.assertEqual(alpha.match_methods, {"new": 3, "guid": 2, "normalized_url": 1})
        self.assertEqual(alpha.guid_present, 4)
        self.assertEqual(alpha.new_articles, 3)
        self.assertEqual(alpha.fields_present,
                         {"title": 3, "summary": 2, "author": 1, "language": 2})
        self.assertEqual(alpha.date_statuses, {"ok": 1, "updated_only": 1, "missing": 1})

        beta = self.source(r, "beta")
        self.assertEqual((beta.fetches, beta.successful_fetches), (3, 2))
        self.assertEqual(beta.outcomes, {"ok": 1, "http_error": 1, "not_modified": 1})
        self.assertEqual(beta.match_methods, {"normalized_url": 1})
        self.assertEqual((beta.guid_present, beta.sightings, beta.new_articles), (1, 1, 0))
        self.assertEqual(beta.fields_present,
                         {"title": 0, "summary": 0, "author": 0, "language": 0})

        gamma = self.source(r, "gamma")
        self.assertEqual((gamma.fetches, gamma.successful_fetches), (4, 0))
        self.assertEqual(gamma.outcomes, {"robots_disallowed": 1, "network_error": 1,
                                          "parse_error": 1, "too_large": 1})
        self.assertEqual(gamma.sightings, 0)

    def test_since_day2(self):
        r = self.report(parse_since("2026-10-02"))
        self.assertEqual(r.since, "2026-10-02T00:00:00Z")
        self.assertEqual(r.runs, {"completed_with_errors": 2, "failed": 1})
        self.assertEqual(r.fetch_count, 8)
        self.assertEqual((r.entries_observed, r.entry_errors), (4, 0))
        self.assertEqual((r.sightings, r.new_articles), (4, 1))
        self.assertEqual(r.articles_all_time, 3)  # not filtered
        self.assertEqual(r.match_methods, {"new": 1, "guid": 2, "normalized_url": 1})
        self.assertEqual(r.cross_source_url_matches, 0)
        self.assertEqual(r.multi_source_articles, 0)
        self.assertEqual(r.multi_sighting_articles, 1)
        # Day 2 is compared with day 1 even though day 1 is outside the period.
        self.assertEqual(r.fingerprint_changes, ChangeCount(2, 1))
        self.assertEqual(r.body_changes, ChangeCount(2, 1))
        self.assertEqual(self.source(r, "alpha").fields_present,
                         {"title": 1, "summary": 1, "author": 0, "language": 1})

    def test_since_day3_unchanged_recurrence_not_counted(self):
        r = self.report(parse_since("2026-10-03"))
        self.assertEqual(r.run_count, 2)
        self.assertEqual(r.fetch_count, 6)
        self.assertEqual(r.fingerprint_changes, ChangeCount(compared=1, changed=0))
        self.assertEqual(r.fingerprint_changed_guids, 0)
        self.assertEqual(r.body_changes, ChangeCount(compared=1, changed=0))
        self.assertEqual([s.source_id for s in r.sources], ["alpha", "beta", "gamma"])

    def test_since_after_all_activity(self):
        r = self.report(parse_since("2026-10-04"))
        self.assertEqual((r.run_count, r.fetch_count, r.sightings, r.new_articles), (0, 0, 0, 0))
        self.assertEqual(r.articles_all_time, 3)
        self.assertEqual(r.sources, ())


class FingerprintEdgeTests(TempDir):
    def test_same_guid_twice_in_one_fetch_is_not_a_change(self):
        b = Builder(self.db)
        run = b.run(DAY1)
        f = b.fetch(run, "alpha", DAY1, body="x", entries=2)
        a = b.article("/1", "alpha", DAY1)
        b.sighting(a, f, "alpha", 0, DAY1, "new", guid="g", sha="one")
        b.sighting(a, f, "alpha", 1, DAY1, "guid", guid="g", sha="two")
        b.close()
        self.assertEqual(self.report().fingerprint_changes, ChangeCount(0, 0))

    def test_same_guid_in_other_source_is_not_compared(self):
        b = Builder(self.db)
        run = b.run(DAY1)
        fa = b.fetch(run, "alpha", DAY1, body="x", entries=1)
        fb = b.fetch(run, "beta", DAY1, body="y", entries=1)
        a = b.article("/1", "alpha", DAY1)
        c = b.article("/2", "beta", DAY1)
        b.sighting(a, fa, "alpha", 0, DAY1, "new", guid="shared", sha="one")
        b.sighting(c, fb, "beta", 0, DAY1, "new", guid="shared", sha="two")
        b.close()
        self.assertEqual(self.report().fingerprint_changes, ChangeCount(0, 0))


class OpenReadOnlyTests(TempDir):
    def test_missing_database(self):
        with self.assertRaises(ReportError):
            open_read_only(self.db)
        self.assertFalse(self.db.exists())

    def test_path_with_space_and_hash(self):
        self.db = self.tmp / "my db #1.sqlite3"
        open_database(self.db).close()
        self.assertEqual(self.report().run_count, 0)

    def test_connection_cannot_write(self):
        open_database(self.db).close()
        conn = open_read_only(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO runs (started_at, status, app_version)"
                             " VALUES ('2026-10-01T00:00:00Z', 'running', 'x')")
        finally:
            conn.close()

    def test_unsupported_version(self):
        conn = open_database(self.db)
        conn.execute("PRAGMA user_version = 2")
        conn.close()
        with self.assertRaisesRegex(SchemaError, "unsupported database schema version 2"):
            open_read_only(self.db)

    def test_no_schema(self):
        sqlite3.connect(self.db).close()  # empty file, user_version 0
        with self.assertRaisesRegex(SchemaError, "no Atlas Signal schema"):
            open_read_only(self.db)

    def test_missing_table(self):
        conn = open_database(self.db)
        conn.execute("DROP TABLE entry_errors")
        conn.close()
        with self.assertRaisesRegex(SchemaError, "missing table 'entry_errors'"):
            open_read_only(self.db)

    def test_missing_column(self):
        conn = open_database(self.db)
        conn.execute("ALTER TABLE articles DROP COLUMN language")
        conn.close()
        with self.assertRaisesRegex(SchemaError, "'articles' is missing columns: language"):
            open_read_only(self.db)

    def test_not_a_database(self):
        self.db.write_bytes(b"this is not sqlite" * 100)
        with self.assertRaises(ReportError):
            open_read_only(self.db)


class ReportCommandTests(TempDir):
    def run_cli(self, *args, db=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["report", "--db", str(db or self.db), *args])
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        """Every file in the temp dir with its bytes and modification time."""
        return {p.name: (p.read_bytes(), os.stat(p).st_mtime_ns) for p in self.tmp.iterdir()}

    def test_readable_output(self):
        build_scenario(self.db)
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Atlas Signal report: all time", out)
        self.assertIn("Runs: 4 (completed 1, completed_with_errors 2, failed 1)", out)
        self.assertIn("Fetches: 10 (ok 4, not_modified 1, http_error 1, network_error 1,"
                      " parse_error 1, robots_disallowed 1, too_large 1)", out)
        self.assertIn("Feed entries observed: 8; entry errors: 1", out)
        self.assertIn("Sightings: 7; new articles: 3; articles in database (all time): 3", out)
        self.assertIn("Match methods: new 3, guid 2, normalized_url 2", out)
        self.assertIn("normalized_url matches: 2 (1 to another source's article, 1 same source)",
                      out)
        self.assertIn("Recurring GUID fingerprints: 1 changed of 2 compared (1 distinct GUIDs"
                      " changed)", out)
        self.assertIn("Feed bodies vs previous successful fetch: 1 changed of 2 compared", out)
        self.assertIn("  alpha\n    fetches 3, successful 3 (ok 3); entries 7; entry errors 1", out)
        self.assertIn("fields present: title 3, summary 2, author 1, language 2;"
                      " date status: ok 1, updated_only 1, missing 1", out)

    def test_since_option(self):
        build_scenario(self.db)
        code, out, _ = self.run_cli("--since", "2026-10-03")
        self.assertEqual(code, 0)
        self.assertIn("Atlas Signal report: since 2026-10-03T00:00:00Z", out)
        self.assertIn("Runs: 2 (completed_with_errors 1, failed 1)", out)

    def test_empty_database_output(self):
        open_database(self.db).close()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("Runs: 0 (none)", out)
        self.assertIn("Sources: no activity in this period", out)

    def test_malformed_since(self):
        build_scenario(self.db)
        before = self.snapshot()
        code, out, err = self.run_cli("--since", "2026-10-32")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("invalid --since date '2026-10-32'", err)
        self.assertEqual(self.snapshot(), before)

    def test_missing_database_creates_nothing(self):
        db = self.tmp / "nested" / "missing.sqlite3"
        code, out, err = self.run_cli(db=db)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("database not found", err)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_unsupported_schema_exit_code(self):
        conn = open_database(self.db)
        conn.execute("PRAGMA user_version = 7")
        conn.close()
        before = self.snapshot()
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("unsupported database schema version 7", err)
        self.assertEqual(self.snapshot(), before)

    def test_uninitialized_database_is_not_initialized(self):
        sqlite3.connect(self.db).execute("CREATE TABLE other (x)").connection.close()
        before = self.snapshot()
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("no Atlas Signal schema", err)
        self.assertEqual(self.snapshot(), before)

    def test_report_leaves_database_byte_for_byte_unchanged(self):
        build_scenario(self.db)
        before = self.snapshot()
        for args in ((), ("--since", "2026-10-02")):
            code, _, _ = self.run_cli(*args)
            self.assertEqual(code, 0)
        self.assertEqual(self.snapshot(), before)  # also: no journal/WAL files created


if __name__ == "__main__":
    unittest.main()
