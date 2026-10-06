"""Experiment 002 corpus loading, on synthetic databases with made-up content."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tests.e002_support import article, build_db
from research.e002 import corpus


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class CorpusTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def build(self, articles, name="corpus.sqlite3"):
        return build_db(self.dir / name, articles)

    def load(self, path):
        return corpus.load_corpus(path, expected_sha256=corpus.file_sha256(path))


class RepresentativeTimeTests(unittest.TestCase):
    def test_fallback_order(self):
        p, u, f = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"
        self.assertEqual(corpus.representative_time("ok", p, u, f), (utc(p), "published_at"))
        self.assertEqual(corpus.representative_time("updated_only", None, u, f),
                         (utc(u), "updated_at"))
        for status in ("missing", "unparseable", "future"):
            with self.subTest(status=status):
                self.assertEqual(corpus.representative_time(status, p, u, f),
                                 (utc(f), "first_seen_at"))

    def test_missing_field_for_status_is_an_error(self):
        with self.assertRaises(corpus.CorpusError):
            corpus.representative_time("ok", None, "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z")
        with self.assertRaises(corpus.CorpusError):
            corpus.representative_time("updated_only", "2026-01-01T00:00:00Z", None,
                                       "2026-01-03T00:00:00Z")


class DerivationTests(unittest.TestCase):
    def test_publisher_domain_and_segments(self):
        url = "https://www.Example.test/News/Live/2026/item"
        self.assertEqual(corpus.publisher_domain(url), "example.test")
        self.assertEqual(corpus.url_path_segments(url), ("news", "live", "2026", "item"))

    def test_issuing_unit(self):
        self.assertEqual(corpus.issuing_unit("https://www.canada.ca/en/widget-agency/news/x.html"),
                         "widget-agency")
        self.assertEqual(corpus.issuing_unit("https://www.canada.ca/fr/agence/nouvelles/x.html"),
                         "agence")
        self.assertIsNone(corpus.issuing_unit("https://www.canada.ca/widget-agency/x.html"))
        self.assertIsNone(corpus.issuing_unit("https://www.canada.ca/en"))
        self.assertIsNone(corpus.issuing_unit("https://example.test/en/widget-agency/x"))

    def test_document_type(self):
        self.assertEqual(corpus.document_type(("News Releases",)), "news releases")
        self.assertEqual(corpus.document_type(("Topic", " backgrounders ")), "backgrounders")
        self.assertIsNone(corpus.document_type(("Topic",)))
        self.assertIsNone(corpus.document_type(()))
        self.assertIsNone(corpus.document_type(("statements", "readouts")))

    def test_format_class(self):
        cases = [
            ("https://example.test/business/live/2026/x", "Markets day", "live"),
            ("https://example.test/business/2026/x", "Markets day – as it happened", "live"),
            ("https://example.test/business/2026/x", "Markets day - business live", "live"),
            ("https://example.test/business/2026/x", "Markets day - live", "live"),
            ("https://example.test/business/2026/x", "Where to live", "standard"),
            ("https://example.test/sounds/play/x", "Widget hour", "programme"),
            ("https://example.test/news/audio/2026/x", "Widget talk - podcast", "audio"),
            ("https://example.test/news/videos/x", "Watch: widgets", "video"),
            ("https://example.test/news/video/x", "Widget clip", "video"),
            ("https://example.test/commentisfree/2026/x", "A view on widgets", "opinion"),
            ("https://example.test/live/audio/x", "Widgets", "live"),
            ("https://example.test/audio/video/x", "Widgets", "audio"),
            ("https://example.test/news/articles/x", "Widgets", "standard"),
        ]
        for url, title, expected in cases:
            with self.subTest(url=url, title=title):
                self.assertEqual(corpus.format_class(url, title), expected)


class ContainerTests(unittest.TestCase):
    def test_positive_forms(self):
        cases = [
            ("https://example.test/business/live/2026/x", "Markets update"),
            ("https://example.test/business/2026/x", "Widget firm rallies - as it happened"),
            ("https://example.test/business/2026/x", "Widget firm rallies – business live"),
            ("https://example.test/business/2026/x", "Widget firm rallies - live"),
            ("https://example.test/sounds/play/x", "Widget hour"),
            ("https://example.test/news/x", "Daily News 02 / 10 / 2026"),
            ("https://example.test/news/x", "News briefing: 3 March 2026"),
            ("https://example.test/news/x", "Round-up, 3 March 2026"),
            ("https://example.test/news/x", "Daily round-up 2026-03-03"),
        ]
        for url, title in cases:
            with self.subTest(url=url, title=title):
                self.assertTrue(corpus.container_flag(url, title))

    def test_ordinary_articles_are_not_containers(self):
        cases = [
            ("https://example.test/news/x", "Widget firm opens plant"),
            ("https://example.test/news/audio/x", "Widget talk - podcast"),
            ("https://example.test/news/videos/x", "Watch: widgets"),
            ("https://example.test/commentisfree/x", "A view on widgets"),
            ("https://example.test/news/x", "Where to live"),
            ("https://example.test/news/x", "Widget firm to go live"),
            ("https://example.test/news/x", "Live music returns to Fooland"),
            ("https://example.test/news/x", "Business news 3 March 2026"),
            ("https://example.test/news/x", "Daily News"),
            ("https://example.test/news/x", "3 March 2026"),
            ("https://example.test/news/x", "Daily News 02/10"),
            ("https://example.test/delivery/x", "Widget news"),
        ]
        for url, title in cases:
            with self.subTest(url=url, title=title):
                self.assertFalse(corpus.container_flag(url, title))

    def test_independent_of_format_precedence(self):
        url = "https://example.test/audio/sounds/x"
        self.assertEqual(corpus.format_class(url, "Widget hour"), "programme")
        self.assertTrue(corpus.container_flag("https://example.test/opinion/live/x", "View"))


class LoadTests(CorpusTestCase):
    def test_records_and_ordering(self):
        path = self.build([
            article(7, source="gov-b", url="https://www.canada.ca/en/widget-agency/news/x.html",
                    date_status="updated_only", published_at=None,
                    updated_at="2026-01-05T10:00:00Z", sightings=((None, ["news releases"]),)),
            article(3, title="Made-up live page - as it happened"),
        ])
        c = self.load(path)
        self.assertEqual([a.id for a in c.articles], [3, 7])
        self.assertEqual(c.sources, (("gov-b", "government"), ("pub-a", "publisher")))
        a3, a7 = c.articles
        self.assertEqual((a3.source_kind, a3.format_class, a3.document_type, a3.issuing_unit),
                         ("publisher", "live", None, None))
        self.assertEqual(a3.representative_time_field, "published_at")
        self.assertEqual((a7.source_kind, a7.document_type, a7.issuing_unit, a7.publisher_domain),
                         ("government", "news releases", "widget-agency", "canada.ca"))
        self.assertEqual(a7.representative_time, utc("2026-01-05T10:00:00Z"))
        self.assertTrue(a3.container_flag)
        self.assertFalse(a7.container_flag)
        self.assertEqual(corpus.template_scope(a7), ("gov-b", "news releases"))
        self.assertEqual(corpus.template_scope(a3), ("pub-a", "live"))

    def test_latest_sighting_is_highest_id_not_insert_order(self):
        path = self.build([
            article(1, sightings=((50, ["latest"]), (10, ["older"]), (30, ["middle"]))),
            article(2, sightings=((20, None),)),
        ])
        c = self.load(path)
        self.assertEqual(c.articles[0].categories, ("latest",))
        self.assertEqual(c.articles[1].categories, ())

    def test_invalid_categories_rejected(self):
        path = self.build([article(1, sightings=((None, {"not": "a list"}),))])
        with self.assertRaises(corpus.CorpusError):
            self.load(path)

    def test_derived_fields_do_not_depend_on_article_ids(self):
        def rows(ids):
            return [article(ids[0], url="https://example.test/news/audio/a", title="Alpha talk"),
                    article(ids[2], url="https://example.test/news/c",
                            title="Daily News 02 / 10 / 2026"),
                    article(ids[1], source="gov-b",
                            url="https://www.canada.ca/en/widget-agency/news/b.html",
                            sightings=((None, ["media advisories"]),))]
        first = self.load(self.build(rows([1, 2, 3]), "a.sqlite3"))
        second = self.load(self.build(rows([902, 17, 40]), "b.sqlite3"))

        def strip(c):
            return sorted((a.normalized_url, a.format_class, a.document_type, a.issuing_unit,
                           a.representative_time, a.categories, a.container_flag)
                          for a in c.articles)
        self.assertEqual(strip(first), strip(second))


class HashAndAccessTests(CorpusTestCase):
    def test_hash_mismatch_rejected_before_opening(self):
        path = self.build([article(1)])
        with mock.patch.object(corpus.sqlite3, "connect") as connect:
            with self.assertRaises(corpus.CorpusHashMismatch):
                corpus.load_corpus(path, expected_sha256="0" * 64)
            connect.assert_not_called()

    def test_default_expected_hash_is_the_frozen_snapshot(self):
        path = self.build([article(1)])
        with self.assertRaises(corpus.CorpusHashMismatch):
            corpus.load_corpus(path)

    def test_uri_is_read_only_and_immutable(self):
        path = self.build([article(1)])
        with mock.patch.object(corpus.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            self.load(path)
        (uri,), kwargs = connect.call_args
        self.assertTrue(uri.startswith("file:"))
        self.assertTrue(uri.endswith("?mode=ro&immutable=1"))
        self.assertEqual(kwargs, {"uri": True})

    def test_connection_cannot_write(self):
        path = self.build([article(1)])
        conn = corpus.connect_readonly(path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM articles")

    def test_loading_leaves_file_unchanged_and_creates_no_sidecars(self):
        path = self.build([article(1), article(2)])
        before = (corpus.file_sha256(path), sorted(p.name for p in self.dir.iterdir()))
        c = self.load(path)
        after = (corpus.file_sha256(path), sorted(p.name for p in self.dir.iterdir()))
        self.assertEqual(before, after)
        self.assertEqual(c.sha256, before[0])

    def test_change_during_read_detected(self):
        path = self.build([article(1)])
        real = corpus.file_sha256(path)
        with mock.patch.object(corpus, "file_sha256", side_effect=[real, "f" * 64]):
            with self.assertRaises(corpus.CorpusHashMismatch):
                corpus.load_corpus(path, expected_sha256=real)


if __name__ == "__main__":
    unittest.main()
