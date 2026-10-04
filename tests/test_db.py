import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_signal import db

TS = "2026-10-04T12:00:00Z"


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "test.sqlite3"

    def open(self):
        conn = db.open_database(self.path)
        self.addCleanup(conn.close)
        return conn

    def raw_connect(self):
        """A plain sqlite3 connection, bypassing db.connect()."""
        return contextlib.closing(sqlite3.connect(self.path))


def table_names(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def index_names(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }


def insert_source(conn, source_id="src-a"):
    conn.execute(
        "INSERT INTO sources (id, name, publisher, kind, feed_url, first_added_at, updated_at)"
        " VALUES (?, 'Source A', 'Publisher A', 'publisher', 'https://example.com/rss', ?, ?)",
        (source_id, TS, TS),
    )


def insert_run(conn):
    return conn.execute(
        "INSERT INTO runs (started_at, status, app_version) VALUES (?, 'running', '0.1.0')",
        (TS,),
    ).lastrowid


def insert_fetch(conn, run_id, source_id="src-a"):
    return conn.execute(
        "INSERT INTO fetches (run_id, source_id, started_at, request_url, outcome)"
        " VALUES (?, ?, ?, 'https://example.com/rss', 'ok')",
        (run_id, source_id, TS),
    ).lastrowid


def insert_article(conn, url="https://example.com/a", source_id="src-a"):
    return conn.execute(
        "INSERT INTO articles (normalized_url, first_source_id, date_status,"
        " first_seen_at, last_seen_at) VALUES (?, ?, 'ok', ?, ?)",
        (url, source_id, TS, TS),
    ).lastrowid


def insert_sighting(conn, article_id, fetch_id, entry_index=0, source_id="src-a"):
    return conn.execute(
        "INSERT INTO sightings (article_id, fetch_id, source_id, entry_index, seen_at,"
        " match_method, url_as_seen, entry_sha256, raw_entry_json)"
        " VALUES (?, ?, ?, ?, ?, 'new', 'https://example.com/a', 'abc', '{}')",
        (article_id, fetch_id, source_id, entry_index, TS),
    ).lastrowid


class InitializationTests(DatabaseTestCase):
    def test_new_database_initializes(self):
        conn = self.open()
        self.assertTrue(self.path.exists())
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_all_tables_exist(self):
        conn = self.open()
        self.assertEqual(table_names(conn), set(db.TABLES))
        self.assertEqual(
            set(db.TABLES),
            {"sources", "runs", "fetches", "articles", "sightings", "entry_errors"},
        )

    def test_all_tables_are_strict(self):
        conn = self.open()
        strict = {
            row[1]: row[5]
            for row in conn.execute("PRAGMA table_list")
            if row[0] == "main" and row[1] in db.TABLES
        }
        self.assertEqual(strict, {name: 1 for name in db.TABLES})

    def test_required_indexes_exist(self):
        conn = self.open()
        self.assertTrue({"sightings_source_guid", "sightings_article"} <= index_names(conn))
        cols = [row[2] for row in conn.execute("PRAGMA index_info(sightings_source_guid)")]
        self.assertEqual(cols, ["source_id", "entry_guid"])
        cols = [row[2] for row in conn.execute("PRAGMA index_info(sightings_article)")]
        self.assertEqual(cols, ["article_id"])

    def test_unique_constraints_are_indexed(self):
        conn = self.open()
        unique_cols = set()
        for table in ("articles", "sightings"):
            for _, name, unique, *_ in conn.execute(f"PRAGMA index_list({table})"):
                if unique:
                    cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({name})"))
                    unique_cols.add((table, cols))
        self.assertIn(("articles", ("normalized_url",)), unique_cols)
        self.assertIn(("sightings", ("fetch_id", "entry_index")), unique_cols)

    def test_user_version_is_one(self):
        conn = self.open()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(db.SCHEMA_VERSION, 1)

    def test_initialize_twice_is_safe(self):
        conn = self.open()
        insert_source(conn)
        conn.commit()
        db.initialize(conn)
        db.initialize(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 1)
        self.assertEqual(table_names(conn), set(db.TABLES))

    def test_existing_version_1_database_reopens(self):
        conn = db.open_database(self.path)
        insert_source(conn)
        conn.commit()
        conn.close()

        conn = self.open()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT id FROM sources").fetchone()[0], "src-a")

    def test_canonical_url_is_nullable(self):
        conn = self.open()
        insert_source(conn)
        article_id = insert_article(conn)
        row = conn.execute("SELECT canonical_url FROM articles WHERE id = ?", (article_id,))
        self.assertIsNone(row.fetchone()[0])


class SchemaRejectionTests(DatabaseTestCase):
    def test_future_version_rejected_and_not_altered(self):
        with self.raw_connect() as raw:
            raw.executescript("CREATE TABLE something_else (x INTEGER); PRAGMA user_version = 2;")

        with self.assertRaises(db.SchemaError):
            db.open_database(self.path)

        with self.raw_connect() as raw:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(table_names(raw), {"something_else"})

    def test_unversioned_database_with_tables_rejected_and_not_altered(self):
        with self.raw_connect() as raw:
            raw.executescript("CREATE TABLE something_else (x INTEGER);")

        with self.assertRaises(db.SchemaError):
            db.open_database(self.path)

        with self.raw_connect() as raw:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(table_names(raw), {"something_else"})

    def test_version_1_with_missing_tables_rejected(self):
        with self.raw_connect() as raw:
            raw.executescript("CREATE TABLE sources (id TEXT); PRAGMA user_version = 1;")

        with self.assertRaises(db.SchemaError):
            db.open_database(self.path)

        with self.raw_connect() as raw:
            self.assertEqual(table_names(raw), {"sources"})

    def test_failed_initialization_rolls_back(self):
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        original = db.SCHEMA_SQL
        db.SCHEMA_SQL = original + "\nCREATE TABLE broken (;"
        try:
            with self.assertRaises(sqlite3.Error):
                db.initialize(conn)
        finally:
            db.SCHEMA_SQL = original
        self.assertFalse(conn.in_transaction)
        self.assertEqual(table_names(conn), set())
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)


class ForeignKeyTests(DatabaseTestCase):
    def test_foreign_keys_enabled_on_every_connection(self):
        first = self.open()
        second = db.connect(self.path)
        self.addCleanup(second.close)
        for conn in (first, second):
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_foreign_keys_stay_enabled_after_initialize(self):
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        db.initialize(conn)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_plain_sqlite_connection_has_foreign_keys_off(self):
        # Documents why db.connect() is needed: SQLite defaults to off.
        self.open()
        with self.raw_connect() as raw:
            self.assertEqual(raw.execute("PRAGMA foreign_keys").fetchone()[0], 0)

    def test_fetch_requires_existing_run_and_source(self):
        conn = self.open()
        insert_source(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            insert_fetch(conn, run_id=999)
        run_id = insert_run(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            insert_fetch(conn, run_id, source_id="no-such-source")

    def test_article_requires_existing_source(self):
        conn = self.open()
        with self.assertRaises(sqlite3.IntegrityError):
            insert_article(conn, source_id="no-such-source")

    def test_sighting_requires_existing_article_and_fetch(self):
        conn = self.open()
        insert_source(conn)
        fetch_id = insert_fetch(conn, insert_run(conn))
        article_id = insert_article(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            insert_sighting(conn, article_id=999, fetch_id=fetch_id)
        with self.assertRaises(sqlite3.IntegrityError):
            insert_sighting(conn, article_id=article_id, fetch_id=999)

    def test_entry_error_requires_existing_fetch(self):
        conn = self.open()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO entry_errors (fetch_id, entry_index, error) VALUES (999, 0, 'x')")

    def test_cannot_delete_referenced_article(self):
        conn = self.open()
        insert_source(conn)
        fetch_id = insert_fetch(conn, insert_run(conn))
        article_id = insert_article(conn)
        insert_sighting(conn, article_id, fetch_id)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))


class ConstraintTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.conn = self.open()
        insert_source(self.conn)
        self.run_id = insert_run(self.conn)
        self.fetch_id = insert_fetch(self.conn, self.run_id)

    def test_strict_typing_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE fetches SET http_status = 'not a number' WHERE id = ?", (self.fetch_id,)
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO sightings (article_id, fetch_id, source_id, entry_index, seen_at,"
                " match_method, url_as_seen, entry_sha256, raw_entry_json)"
                " VALUES (1, ?, 'src-a', 'first', ?, 'new', 'u', 'h', '{}')",
                (self.fetch_id, TS),
            )

    def test_normalized_url_unique(self):
        insert_article(self.conn, url="https://example.com/a")
        with self.assertRaises(sqlite3.IntegrityError):
            insert_article(self.conn, url="https://example.com/a")
        insert_article(self.conn, url="https://example.com/b")

    def test_sighting_fetch_entry_index_unique(self):
        article_id = insert_article(self.conn)
        insert_sighting(self.conn, article_id, self.fetch_id, entry_index=0)
        with self.assertRaises(sqlite3.IntegrityError):
            insert_sighting(self.conn, article_id, self.fetch_id, entry_index=0)
        insert_sighting(self.conn, article_id, self.fetch_id, entry_index=1)
        other_fetch = insert_fetch(self.conn, self.run_id)
        insert_sighting(self.conn, article_id, other_fetch, entry_index=0)

    def test_not_null_constraints(self):
        cases = {
            "article without normalized_url":
                "INSERT INTO articles (first_source_id, date_status, first_seen_at, last_seen_at)"
                f" VALUES ('src-a', 'ok', '{TS}', '{TS}')",
            "article without first_seen_at":
                "INSERT INTO articles (normalized_url, first_source_id, date_status, last_seen_at)"
                f" VALUES ('https://example.com/x', 'src-a', 'ok', '{TS}')",
            "run without status":
                f"INSERT INTO runs (started_at, app_version) VALUES ('{TS}', '0.1.0')",
            "fetch without outcome":
                "INSERT INTO fetches (run_id, source_id, started_at, request_url)"
                f" VALUES ({self.run_id}, 'src-a', '{TS}', 'https://example.com/rss')",
            "sighting without url_as_seen":
                "INSERT INTO sightings (article_id, fetch_id, source_id, entry_index, seen_at,"
                " match_method, entry_sha256, raw_entry_json)"
                f" VALUES (1, {self.fetch_id}, 'src-a', 5, '{TS}', 'new', 'h', '{{}}')",
            "entry error without error text":
                f"INSERT INTO entry_errors (fetch_id, entry_index) VALUES ({self.fetch_id}, 0)",
            "source without feed_url":
                "INSERT INTO sources (id, name, publisher, kind, first_added_at, updated_at)"
                f" VALUES ('src-b', 'B', 'B', 'publisher', '{TS}', '{TS}')",
        }
        for label, sql in cases.items():
            with self.subTest(label):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "NOT NULL"):
                    self.conn.execute(sql)

    def test_optional_columns_accept_null(self):
        article_id = insert_article(self.conn)
        row = self.conn.execute(
            "SELECT canonical_url, title, summary, author, language, published_at, updated_at"
            " FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        self.assertEqual(row, (None,) * 7)

    def test_timestamp_format_enforced(self):
        for bad in ["2026-10-04 12:00:00", "2026-10-04T12:00:00+00:00", "2026-10-04T12:00:00.123Z",
                    "2026-10-04", "yesterday"]:
            with self.subTest(bad=bad):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(
                        "INSERT INTO runs (started_at, status, app_version) VALUES (?, 'running', 'x')",
                        (bad,),
                    )

    def test_nullable_timestamp_accepts_null_but_checks_format(self):
        self.conn.execute("UPDATE runs SET finished_at = NULL WHERE id = ?", (self.run_id,))
        self.conn.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (TS, self.run_id))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE runs SET finished_at = 'soon' WHERE id = ?", (self.run_id,))

    def test_enumerated_values_enforced(self):
        cases = [
            ("UPDATE runs SET status = 'paused' WHERE id = ?", self.run_id),
            ("UPDATE fetches SET outcome = 'maybe' WHERE id = ?", self.fetch_id),
            ("UPDATE sources SET kind = 'blog' WHERE id = ?", "src-a"),
            ("UPDATE sources SET enabled = 2 WHERE id = ?", "src-a"),
            ("UPDATE fetches SET bozo = 5 WHERE id = ?", self.fetch_id),
        ]
        for sql, key in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(sql, (key,))
        article_id = insert_article(self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE articles SET date_status = 'soon' WHERE id = ?", (article_id,))


if __name__ == "__main__":
    unittest.main()
