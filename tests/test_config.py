import dataclasses
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path

from atlas_signal import db
from atlas_signal.config import (
    ConfigError,
    SourceConfig,
    load_sources,
    parse_sources,
    sync_sources,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "config" / "sources.toml"

T1 = "2026-10-04T12:00:00Z"
T2 = "2026-10-05T08:30:00Z"
T3 = "2026-10-06T09:45:00Z"

VALID_ENTRY = {
    "id": "example-news",
    "name": "Example News",
    "publisher": "Example",
    "kind": "publisher",
    "feed_url": "https://example.com/rss.xml",
}


def entry(**overrides):
    data = dict(VALID_ENTRY)
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_toml(self, text):
        path = self.tmp / "sources.toml"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path


class RealConfigTests(unittest.TestCase):
    def test_loads_six_sources_in_file_order(self):
        sources = load_sources(REAL_CONFIG)
        self.assertEqual(
            [s.id for s in sources],
            [
                "bbc-business",
                "cbc-business",
                "guardian-business",
                "guardian-economics",
                "ec-press",
                "gc-news",
            ],
        )

    def test_real_sources_details(self):
        by_id = {s.id: s for s in load_sources(REAL_CONFIG)}
        self.assertEqual(by_id["bbc-business"].feed_url, "https://feeds.bbci.co.uk/news/business/rss.xml")
        self.assertEqual(by_id["cbc-business"].feed_url, "https://www.cbc.ca/webfeed/rss/rss-business")
        self.assertEqual(by_id["guardian-business"].feed_url, "https://www.theguardian.com/business/rss")
        self.assertEqual(
            by_id["guardian-economics"].feed_url, "https://www.theguardian.com/business/economics/rss"
        )
        self.assertEqual(
            by_id["ec-press"].feed_url, "https://ec.europa.eu/commission/presscorner/api/rss?language=en"
        )
        self.assertEqual(
            by_id["gc-news"].feed_url,
            "https://api.io.canada.ca/io-server/gc/news/en/v2"
            "?sort=publishedDate&orderBy=desc&pick=50&format=atom",
        )
        kinds = {s.id: s.kind for s in by_id.values()}
        self.assertEqual(
            {k for k, v in kinds.items() if v == "government"}, {"ec-press", "gc-news"}
        )
        self.assertTrue(all(s.enabled for s in by_id.values()))

    def test_source_config_is_immutable(self):
        source = load_sources(REAL_CONFIG)[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            source.name = "changed"


class LoadSourcesTests(TempDirTestCase):
    def test_order_preserved(self):
        path = self.write_toml(
            """
            [[sources]]
            id = "zeta"
            name = "Z"
            publisher = "Z"
            kind = "publisher"
            feed_url = "https://z.example/rss"

            [[sources]]
            id = "alpha"
            name = "A"
            publisher = "A"
            kind = "government"
            feed_url = "https://a.example/rss"
            """
        )
        self.assertEqual([s.id for s in load_sources(path)], ["zeta", "alpha"])

    def test_enabled_defaults_true_and_false_respected(self):
        path = self.write_toml(
            """
            [[sources]]
            id = "on"
            name = "On"
            publisher = "P"
            kind = "publisher"
            feed_url = "https://on.example/rss"

            [[sources]]
            id = "off"
            name = "Off"
            publisher = "P"
            kind = "publisher"
            feed_url = "https://off.example/rss"
            enabled = false
            """
        )
        on, off = load_sources(path)
        self.assertTrue(on.enabled)
        self.assertFalse(off.enabled)

    def test_malformed_toml(self):
        path = self.write_toml('[[sources]]\nid = "unterminated\n')
        with self.assertRaisesRegex(ConfigError, "not valid TOML"):
            load_sources(path)

    def test_invalid_utf8(self):
        path = self.tmp / "sources.toml"
        path.write_bytes(b'[[sources]]\nname = "\xff"\n')
        with self.assertRaisesRegex(ConfigError, "UTF-8"):
            load_sources(path)

    def test_missing_file(self):
        with self.assertRaisesRegex(ConfigError, "cannot read"):
            load_sources(self.tmp / "nope.toml")

    def test_error_message_names_file(self):
        path = self.write_toml('[[sources]]\nid = "x"\n')
        with self.assertRaises(ConfigError) as ctx:
            load_sources(path)
        self.assertIn("sources.toml", str(ctx.exception))
        self.assertIn("missing required fields", str(ctx.exception))


class ParseSourcesTests(unittest.TestCase):
    def assertConfigError(self, data, pattern):
        with self.assertRaisesRegex(ConfigError, pattern):
            parse_sources(data)

    def test_valid_entry(self):
        (source,) = parse_sources({"sources": [entry()]})
        self.assertEqual(
            source,
            SourceConfig("example-news", "Example News", "Example", "publisher",
                         "https://example.com/rss.xml", True),
        )

    def test_no_sources(self):
        self.assertConfigError({}, "no \\[\\[sources\\]\\]")
        self.assertConfigError({"sources": []}, "no \\[\\[sources\\]\\]")

    def test_sources_not_array(self):
        self.assertConfigError({"sources": {"id": "x"}}, "array of tables")

    def test_entry_not_table(self):
        self.assertConfigError({"sources": ["bbc"]}, "must be a table")

    def test_unknown_top_level_key(self):
        self.assertConfigError({"sources": [entry()], "extra": 1}, "unknown top-level")

    def test_unknown_field(self):
        self.assertConfigError({"sources": [entry(url="https://x.example/")]}, "unknown fields: url")

    def test_missing_required_fields(self):
        for field in ("id", "name", "publisher", "kind", "feed_url"):
            with self.subTest(field=field):
                self.assertConfigError(
                    {"sources": [entry(**{field: None})]}, f"missing required fields: {field}"
                )

    def test_wrong_field_types(self):
        for field, value in [
            ("id", 5),
            ("name", ["BBC"]),
            ("publisher", {"x": 1}),
            ("kind", True),
            ("feed_url", 3.5),
        ]:
            with self.subTest(field=field):
                self.assertConfigError({"sources": [entry(**{field: value})]}, "must be a string")

    def test_enabled_must_be_boolean(self):
        for value in ("true", 1, 0):
            with self.subTest(value=value):
                self.assertConfigError({"sources": [entry(enabled=value)]}, "'enabled' must be true or false")

    def test_empty_and_padded_strings(self):
        self.assertConfigError({"sources": [entry(name="   ")]}, "must not be empty")
        self.assertConfigError({"sources": [entry(name="")]}, "must not be empty")
        self.assertConfigError({"sources": [entry(name=" BBC")]}, "whitespace")
        self.assertConfigError({"sources": [entry(feed_url="https://x.example/ ")]}, "whitespace")

    def test_duplicate_ids(self):
        self.assertConfigError(
            {"sources": [entry(), entry(name="Other", feed_url="https://other.example/rss")]},
            "duplicate id 'example-news'",
        )

    def test_invalid_ids(self):
        for bad in ["BBC", "bbc_business", "bbc business", "-bbc", "bbc-", "bbc--business",
                    "bbc.business", "bbc/business", "émission", "a" * 65]:
            with self.subTest(bad=bad):
                self.assertConfigError({"sources": [entry(id=bad)]}, "invalid id")

    def test_valid_ids(self):
        for good in ["bbc", "bbc-business", "gc-news-2", "a1", "a" * 64]:
            with self.subTest(good=good):
                self.assertEqual(parse_sources({"sources": [entry(id=good)]})[0].id, good)

    def test_unsupported_kind(self):
        for bad in ["blog", "aggregator", "Publisher", "GOVERNMENT"]:
            with self.subTest(bad=bad):
                self.assertConfigError({"sources": [entry(kind=bad)]}, "unsupported kind")

    def test_invalid_feed_urls(self):
        for bad in [
            "/rss.xml",
            "rss.xml",
            "//example.com/rss",
            "www.example.com/rss",
            "ftp://example.com/rss",
            "feed://example.com/rss",
            "file:///C:/rss.xml",
            "https://",
            "https:example.com/rss",
            "https://example.com:notaport/rss",
            "https://user:pw@example.com/rss",
            "https://example.com/r ss",
        ]:
            with self.subTest(bad=bad):
                self.assertConfigError({"sources": [entry(feed_url=bad)]}, "feed_url")

    def test_feed_url_kept_exactly(self):
        url = "http://Example.COM:80/feed/?utm_source=x&b=2&a=1#frag"
        (source,) = parse_sources({"sources": [entry(feed_url=url)]})
        self.assertEqual(source.feed_url, url)

    def test_error_names_entry(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_sources({"sources": [entry(), entry(id="second", kind="blog")]})
        self.assertIn("source #2 ('second')", str(ctx.exception))


def source(source_id, **overrides):
    values = dict(
        id=source_id,
        name=f"Name {source_id}",
        publisher=f"Publisher {source_id}",
        kind="publisher",
        feed_url=f"https://{source_id}.example/rss",
        enabled=True,
    )
    values.update(overrides)
    return SourceConfig(**values)


class SyncSourcesTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.conn = db.open_database(self.tmp / "test.sqlite3")
        self.addCleanup(self.conn.close)

    def rows(self):
        self.conn.row_factory = sqlite3.Row
        try:
            return {
                row["id"]: dict(row)
                for row in self.conn.execute("SELECT * FROM sources ORDER BY id")
            }
        finally:
            self.conn.row_factory = None

    def test_initial_sync_inserts_all_real_sources(self):
        sources = load_sources(REAL_CONFIG)
        result = sync_sources(self.conn, sources, now=T1)
        self.assertEqual(result.inserted, tuple(s.id for s in sources))
        self.assertEqual((result.updated, result.disabled, result.unchanged), ((), (), ()))
        rows = self.rows()
        self.assertEqual(set(rows), {s.id for s in sources})
        for s in sources:
            row = rows[s.id]
            self.assertEqual(
                (row["name"], row["publisher"], row["kind"], row["feed_url"], row["enabled"]),
                (s.name, s.publisher, s.kind, s.feed_url, 1),
            )
            self.assertEqual((row["first_added_at"], row["updated_at"]), (T1, T1))

    def test_second_sync_does_not_duplicate_or_touch_rows(self):
        sources = load_sources(REAL_CONFIG)
        sync_sources(self.conn, sources, now=T1)
        result = sync_sources(self.conn, sources, now=T2)
        self.assertEqual(result.unchanged, tuple(s.id for s in sources))
        self.assertEqual((result.inserted, result.updated, result.disabled), ((), (), ()))
        rows = self.rows()
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["updated_at"] == T1 for r in rows.values()))

    def test_changed_metadata_updates_row_keeps_first_added(self):
        sync_sources(self.conn, [source("a"), source("b")], now=T1)
        changed = source("a", name="Renamed", publisher="New Owner", kind="government",
                         feed_url="https://a.example/rss?pick=50")
        result = sync_sources(self.conn, [changed, source("b")], now=T2)
        self.assertEqual(result.updated, ("a",))
        self.assertEqual(result.unchanged, ("b",))
        row = self.rows()["a"]
        self.assertEqual(
            (row["name"], row["publisher"], row["kind"], row["feed_url"]),
            ("Renamed", "New Owner", "government", "https://a.example/rss?pick=50"),
        )
        self.assertEqual((row["first_added_at"], row["updated_at"]), (T1, T2))
        self.assertEqual(self.rows()["b"]["updated_at"], T1)

    def test_removed_source_disabled_not_deleted(self):
        sync_sources(self.conn, [source("a"), source("b")], now=T1)
        result = sync_sources(self.conn, [source("b")], now=T2)
        self.assertEqual(result.disabled, ("a",))
        rows = self.rows()
        self.assertEqual(set(rows), {"a", "b"})
        self.assertEqual(rows["a"]["enabled"], 0)
        self.assertEqual((rows["a"]["first_added_at"], rows["a"]["updated_at"]), (T1, T2))

        # Already-disabled rows are not touched again.
        result = sync_sources(self.conn, [source("b")], now=T3)
        self.assertEqual(result.disabled, ())
        self.assertEqual(self.rows()["a"]["updated_at"], T2)

    def test_removed_source_with_history_is_kept(self):
        sync_sources(self.conn, [source("a")], now=T1)
        run_id = self.conn.execute(
            "INSERT INTO runs (started_at, status, app_version) VALUES (?, 'completed', 'x')", (T1,)
        ).lastrowid
        self.conn.execute(
            "INSERT INTO fetches (run_id, source_id, started_at, request_url, outcome)"
            " VALUES (?, 'a', ?, 'https://a.example/rss', 'ok')",
            (run_id, T1),
        )
        self.conn.commit()
        sync_sources(self.conn, [source("b")], now=T2)
        self.assertEqual(self.rows()["a"]["enabled"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fetches").fetchone()[0], 1)

    def test_readded_source_is_reenabled(self):
        sync_sources(self.conn, [source("a")], now=T1)
        sync_sources(self.conn, [], now=T2)
        result = sync_sources(self.conn, [source("a")], now=T3)
        self.assertEqual(result.updated, ("a",))
        row = self.rows()["a"]
        self.assertEqual((row["enabled"], row["first_added_at"], row["updated_at"]), (1, T1, T3))

    def test_explicitly_disabled_source(self):
        result = sync_sources(self.conn, [source("a", enabled=False)], now=T1)
        self.assertEqual(result.inserted, ("a",))
        self.assertEqual(self.rows()["a"]["enabled"], 0)

        result = sync_sources(self.conn, [source("a", enabled=False)], now=T2)
        self.assertEqual(result.unchanged, ("a",))

        result = sync_sources(self.conn, [source("a", enabled=True)], now=T3)
        self.assertEqual(result.updated, ("a",))
        self.assertEqual(self.rows()["a"]["enabled"], 1)

        result = sync_sources(self.conn, [source("a", enabled=False)], now=T3)
        self.assertEqual(result.updated, ("a",))
        self.assertEqual(self.rows()["a"]["enabled"], 0)

    def test_default_timestamp_has_schema_format(self):
        sync_sources(self.conn, [source("a")])
        stamp = self.rows()["a"]["first_added_at"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_rollback_on_failure_during_insert(self):
        bad = source("c", kind="blog")  # bypasses config validation; violates schema CHECK
        with self.assertRaises(sqlite3.IntegrityError):
            sync_sources(self.conn, [source("a"), source("b"), bad], now=T1)
        self.assertEqual(self.rows(), {})
        self.assertFalse(self.conn.in_transaction)

    def test_rollback_on_failure_keeps_previous_state(self):
        sync_sources(self.conn, [source("a"), source("b")], now=T1)
        before = self.rows()
        bad = source("z", kind="blog")
        with self.assertRaises(sqlite3.IntegrityError):
            sync_sources(self.conn, [source("a", name="Changed"), bad], now=T2)
        self.assertEqual(self.rows(), before)

    def test_rollback_on_invalid_timestamp(self):
        with self.assertRaises(sqlite3.IntegrityError):
            sync_sources(self.conn, [source("a")], now="not a timestamp")
        self.assertEqual(self.rows(), {})

    def test_duplicate_ids_rejected_before_writing(self):
        with self.assertRaises(ConfigError):
            sync_sources(self.conn, [source("a"), source("a")], now=T1)
        self.assertEqual(self.rows(), {})


class UtcTimestampTests(unittest.TestCase):
    def test_formats_aware_datetimes_in_utc(self):
        from datetime import datetime, timedelta, timezone

        moment = datetime(2026, 10, 4, 9, 2, 48, 123456, tzinfo=timezone(timedelta(hours=-4)))
        self.assertEqual(db.utc_timestamp(moment), "2026-10-04T13:02:48Z")

    def test_rejects_naive_datetime(self):
        from datetime import datetime

        with self.assertRaises(ValueError):
            db.utc_timestamp(datetime(2026, 10, 4, 12, 0, 0))

    def test_default_is_now_in_format(self):
        self.assertRegex(db.utc_timestamp(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()
