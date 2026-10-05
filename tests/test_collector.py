"""Offline tests for the collection pipeline, using a fake downloader."""

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas_signal import db
from atlas_signal.collector import run_collection, run_status
from atlas_signal.config import SourceConfig, sync_sources
from atlas_signal.download import DownloadResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Clock:
    """Deterministic timestamps one second apart."""

    def __init__(self, start="2026-10-04T12:00:00Z"):
        self.current = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    def __call__(self):
        self.current += timedelta(seconds=1)
        return self.current.strftime("%Y-%m-%dT%H:%M:%SZ")

    def jump(self, **delta):
        self.current += timedelta(**delta)


def ok(body, headers=None, status=200, url=""):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return DownloadResult(url=url, final_url=url or None, status=status,
                          headers={k.lower(): v for k, v in (headers or {}).items()}, body=body)


ROBOTS_OPEN = ok("User-agent: *\nDisallow:\n")


class FakeWeb:
    """Maps URL -> response (or a list of responses served in order)."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def __call__(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        response = self.routes.get(url)
        if response is None and url.endswith("/robots.txt"):
            return ROBOTS_OPEN
        if isinstance(response, list):
            response = response.pop(0) if len(response) > 1 else response[0]
        if isinstance(response, Exception):
            raise response
        if response is None:
            return DownloadResult(url=url, status=404)
        return response

    def feed_calls(self, url):
        return [h for u, h in self.calls if u == url]


def rss(*items, language="en"):
    body = "".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>T</title>'
        f"<language>{language}</language>{body}</channel></rss>"
    )


def item(title, link=None, guid=None, pubdate="Fri, 02 Oct 2026 10:00:00 GMT",
         description=None, guid_permalink=False):
    parts = [f"<title>{title}</title>"]
    if link:
        parts.append(f"<link>{link}</link>")
    if guid:
        flag = "true" if guid_permalink else "false"
        parts.append(f'<guid isPermaLink="{flag}">{guid}</guid>')
    if pubdate:
        parts.append(f"<pubDate>{pubdate}</pubDate>")
    if description:
        parts.append(f"<description>{description}</description>")
    return "<item>" + "".join(parts) + "</item>"


def source(source_id, url=None, kind="publisher"):
    return SourceConfig(
        id=source_id, name=source_id, publisher=source_id, kind=kind,
        feed_url=url or f"https://{source_id}.example/rss",
    )


class CollectorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.open_database(Path(self._tmp.name) / "test.sqlite3")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.clock = Clock()
        self.sleeps = []

    def collect(self, web, sources):
        sources = list(sources)
        sync_sources(self.conn, sources, now="2026-10-04T00:00:00Z")
        return run_collection(self.conn, sources, downloader=web, now=self.clock,
                              sleep=self.sleeps.append)

    def count(self, table, where="1=1", params=()):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]

    def fetches(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM fetches ORDER BY id")]

    def articles(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM articles ORDER BY id")]

    def sightings(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM sightings ORDER BY id")]


class FirstCollectionTests(CollectorTestCase):
    def test_successful_first_collection(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: ok(rss(
            item("One", "https://alpha.example/1?utm_source=rss", guid="g1"),
            item("Two", "https://alpha.example/2", guid="g2"),
        ), headers={"ETag": '"v1"', "Last-Modified": "Fri, 02 Oct 2026 10:00:00 GMT"},
            url=a.feed_url)})
        summary = self.collect(web, [a])

        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.exit_code, 0)
        (outcome,) = summary.sources
        self.assertEqual((outcome.outcome, outcome.entry_count, outcome.new_article_count,
                          outcome.sighting_count, outcome.entry_error_count),
                         ("ok", 2, 2, 2, 0))

        run = dict(self.conn.execute("SELECT * FROM runs").fetchone())
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["app_version"], "0.1.0")

        (fetch,) = self.fetches()
        self.assertEqual(fetch["outcome"], "ok")
        self.assertEqual(fetch["http_status"], 200)
        self.assertEqual(fetch["etag"], '"v1"')
        self.assertEqual(fetch["last_modified"], "Fri, 02 Oct 2026 10:00:00 GMT")
        self.assertEqual(fetch["entry_count"], 2)
        self.assertEqual(fetch["new_article_count"], 2)
        self.assertEqual(fetch["entry_error_count"], 0)
        self.assertEqual(len(fetch["body_sha256"]), 64)
        self.assertGreater(fetch["body_bytes"], 0)
        self.assertEqual(fetch["bozo"], 0)
        self.assertIsNotNone(fetch["finished_at"])

        articles = self.articles()
        self.assertEqual([x["normalized_url"] for x in articles],
                         ["https://alpha.example/1", "https://alpha.example/2"])
        self.assertTrue(all(x["canonical_url"] is None for x in articles))
        self.assertEqual(articles[0]["title"], "One")
        self.assertEqual(articles[0]["first_source_id"], "alpha")
        self.assertEqual(articles[0]["date_status"], "ok")
        self.assertEqual(articles[0]["published_at"], "2026-10-02T10:00:00Z")
        self.assertEqual(articles[0]["first_seen_at"], articles[0]["last_seen_at"])

        sightings = self.sightings()
        self.assertEqual([s["match_method"] for s in sightings], ["new", "new"])
        self.assertEqual(sightings[0]["url_as_seen"], "https://alpha.example/1?utm_source=rss")
        self.assertEqual(sightings[0]["entry_guid"], "g1")
        self.assertEqual(sightings[0]["entry_index"], 0)
        self.assertEqual(json.loads(sightings[0]["categories_json"]), [])
        self.assertEqual(len(sightings[0]["entry_sha256"]), 64)
        json.loads(sightings[0]["raw_entry_json"])

    def test_fixture_feeds_collect(self):
        sources = [source("bbc"), source("cbc"), source("ec", kind="government"),
                   source("gc", kind="government")]
        web = FakeWeb({
            sources[0].feed_url: ok((FIXTURES / "bbc_style.rss").read_bytes()),
            sources[1].feed_url: ok((FIXTURES / "cbc_style.rss").read_bytes()),
            sources[2].feed_url: ok((FIXTURES / "ec_style.rss").read_bytes()),
            sources[3].feed_url: ok((FIXTURES / "gc_style.atom").read_bytes()),
        })
        summary = self.collect(web, sources)
        self.assertEqual(summary.status, "completed")
        self.assertEqual(self.count("articles"), 6)
        gc = self.conn.execute(
            "SELECT * FROM articles WHERE first_source_id = 'gc' ORDER BY id").fetchone()
        self.assertEqual(gc["date_status"], "updated_only")
        self.assertIsNone(gc["published_at"])
        self.assertEqual(gc["updated_at"], "2026-10-03T13:02:48Z")
        cbc = self.conn.execute("SELECT * FROM sightings WHERE source_id = 'cbc'").fetchone()
        self.assertEqual(json.loads(cbc["categories_json"]), ["Business", "News/Business"])
        self.assertEqual(cbc["author_raw"], "Alex   Example,  Sam Sample")


class RepeatAndMatchingTests(CollectorTestCase):
    def test_identical_second_run_creates_no_duplicates(self):
        a = source("alpha")
        body = rss(item("One", "https://alpha.example/1", guid="g1"),
                   item("Two", "https://alpha.example/2", guid="g2"))
        web = FakeWeb({a.feed_url: ok(body)})
        self.collect(web, [a])
        summary = self.collect(web, [a])

        self.assertEqual(summary.sources[0].new_article_count, 0)
        self.assertEqual(self.count("articles"), 2)
        self.assertEqual(self.count("runs"), 2)
        self.assertEqual(self.count("fetches"), 2)
        sightings = self.sightings()
        self.assertEqual(len(sightings), 4)
        self.assertEqual([s["match_method"] for s in sightings[2:]], ["guid", "guid"])
        fetch_ids = {s["fetch_id"] for s in sightings}
        self.assertEqual(len(fetch_ids), 2)
        # Same content -> same fingerprint across fetches.
        self.assertEqual(sightings[0]["entry_sha256"], sightings[2]["entry_sha256"])

    def test_items_without_guid_match_by_normalized_url(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: ok(rss(item("One", "https://alpha.example/1")))})
        self.collect(web, [a])
        self.collect(web, [a])
        self.assertEqual(self.count("articles"), 1)
        self.assertEqual([s["match_method"] for s in self.sightings()], ["new", "normalized_url"])

    def test_same_source_guid_match_even_when_link_changes(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("One", "https://alpha.example/1", guid="g1"))),
            ok(rss(item("One", "https://alpha.example/1-renamed", guid="g1"))),
        ]})
        self.collect(web, [a])
        self.collect(web, [a])
        self.assertEqual(self.count("articles"), 1)
        last = self.sightings()[-1]
        self.assertEqual(last["match_method"], "guid")
        self.assertEqual(last["url_as_seen"], "https://alpha.example/1-renamed")
        self.assertEqual(self.articles()[0]["normalized_url"], "https://alpha.example/1")

    def test_guid_match_is_per_source(self):
        a, b = source("alpha"), source("beta")
        web = FakeWeb({
            a.feed_url: ok(rss(item("A", "https://alpha.example/x", guid="shared-guid"))),
            b.feed_url: ok(rss(item("B", "https://beta.example/y", guid="shared-guid"))),
        })
        self.collect(web, [a, b])
        self.assertEqual(self.count("articles"), 2)

    def test_cross_source_normalized_url_match_guardian_style(self):
        business = source("guardian-business", "https://www.news.example/business/rss")
        economics = source("guardian-economics", "https://www.news.example/business/economics/rss")
        shared = "https://www.news.example/business/2026/oct/03/shared-story"
        web = FakeWeb({
            business.feed_url: ok(rss(
                item("Shared story", shared, guid=shared, guid_permalink=True),
                item("Business only", "https://www.news.example/business/only"),
            )),
            economics.feed_url: ok(rss(
                item("Shared story (econ)", shared + "?CMP=econ_rss", guid=shared + "#econ"),
            )),
        })
        summary = self.collect(web, [business, economics])
        self.assertEqual(summary.sources[1].new_article_count, 0)
        self.assertEqual(self.count("articles"), 2)
        shared_article = self.conn.execute(
            "SELECT * FROM articles WHERE normalized_url = ?", (shared,)).fetchone()
        self.assertEqual(shared_article["first_source_id"], "guardian-business")
        self.assertEqual(shared_article["title"], "Shared story")
        rows = self.conn.execute(
            "SELECT source_id, match_method FROM sightings WHERE article_id = ? ORDER BY id",
            (shared_article["id"],)).fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [("guardian-business", "new"), ("guardian-economics", "normalized_url")])
        # Same-host pause between the two feeds (robots fetched once for the host).
        self.assertEqual(len([u for u, _ in web.calls if u.endswith("/robots.txt")]), 1)
        self.assertTrue(self.sleeps)

    def test_tracking_parameters_disappear(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("One", "https://alpha.example/1?at_medium=RSS&at_campaign=rss"))),
            ok(rss(item("One", "http://ALPHA.example/1?utm_source=x#top"))),
        ]})
        self.collect(web, [a])
        self.collect(web, [a])
        self.assertEqual(self.count("articles"), 1)
        self.assertEqual(self.articles()[0]["normalized_url"], "https://alpha.example/1")
        self.assertEqual([s["url_as_seen"] for s in self.sightings()],
                         ["https://alpha.example/1?at_medium=RSS&at_campaign=rss",
                          "http://ALPHA.example/1?utm_source=x#top"])

    def test_changed_entry_same_guid_new_sighting_first_metadata_kept(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("Original headline", "https://alpha.example/1", guid="g1",
                        description="First summary"))),
            ok(rss(item("Corrected headline", "https://alpha.example/1", guid="g1",
                        description="Second summary"))),
        ]})
        self.collect(web, [a])
        first_article = self.articles()[0]
        self.clock.jump(hours=3)
        self.collect(web, [a])

        self.assertEqual(self.count("articles"), 1)
        article = self.articles()[0]
        self.assertEqual(article["title"], "Original headline")
        self.assertEqual(article["summary"], "First summary")
        self.assertEqual(article["first_seen_at"], first_article["first_seen_at"])
        self.assertGreater(article["last_seen_at"], first_article["last_seen_at"])

        first, second = self.sightings()
        self.assertEqual(second["match_method"], "guid")
        self.assertEqual(second["title_raw"], "Corrected headline")
        self.assertNotEqual(first["entry_sha256"], second["entry_sha256"])

    def test_last_seen_updates_on_each_sighting(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: ok(rss(item("One", "https://alpha.example/1")))})
        self.collect(web, [a])
        seen = [self.articles()[0]["last_seen_at"]]
        for _ in range(2):
            self.clock.jump(minutes=30)
            self.collect(web, [a])
            seen.append(self.articles()[0]["last_seen_at"])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), 3)
        self.assertEqual(self.articles()[0]["first_seen_at"], seen[0])

    def test_duplicate_item_within_one_feed(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: ok(rss(
            item("One", "https://alpha.example/1"),
            item("One again", "https://alpha.example/1?utm_source=dup"),
        ))})
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].new_article_count, 1)
        self.assertEqual(self.count("articles"), 1)
        self.assertEqual([s["match_method"] for s in self.sightings()], ["new", "normalized_url"])


class DateResolutionTests(CollectorTestCase):
    def test_future_published_date(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: ok(rss(
            item("Far future", "https://alpha.example/f", pubdate="Tue, 06 Oct 2026 12:00:00 GMT"),
            item("Slightly ahead", "https://alpha.example/s", pubdate="Sun, 04 Oct 2026 20:00:00 GMT"),
            item("No date", "https://alpha.example/n", pubdate=None),
        ))})
        self.collect(web, [a])
        statuses = {r["normalized_url"]: r["date_status"] for r in self.articles()}
        self.assertEqual(statuses, {
            "https://alpha.example/f": "future",
            "https://alpha.example/s": "ok",
            "https://alpha.example/n": "missing",
        })
        future = self.conn.execute(
            "SELECT published_at FROM articles WHERE normalized_url = 'https://alpha.example/f'"
        ).fetchone()[0]
        self.assertEqual(future, "2026-10-06T12:00:00Z")


class EntryErrorTests(CollectorTestCase):
    def test_entry_errors_persisted_good_entries_kept(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: ok((FIXTURES / "no_url.rss").read_bytes())})
        summary = self.collect(web, [a])
        outcome = summary.sources[0]
        self.assertEqual(outcome.outcome, "ok")
        self.assertEqual((outcome.entry_count, outcome.new_article_count, outcome.entry_error_count),
                         (6, 3, 3))
        self.assertEqual(summary.status, "completed")
        errors = [dict(r) for r in self.conn.execute("SELECT * FROM entry_errors ORDER BY entry_index")]
        self.assertEqual([e["entry_index"] for e in errors], [1, 2, 4])
        self.assertEqual(errors[0]["entry_guid"], "item-0002")
        self.assertEqual(errors[2]["link_raw"], "/relative/path")
        self.assertTrue(all(e["fetch_id"] == self.fetches()[0]["id"] for e in errors))
        self.assertEqual(self.fetches()[0]["entry_error_count"], 3)


class FailureIsolationTests(CollectorTestCase):
    def test_one_source_failure_does_not_stop_later_sources(self):
        a, b, c = source("alpha"), source("beta"), source("gamma")
        web = FakeWeb({
            a.feed_url: ok(rss(item("A", "https://alpha.example/1"))),
            b.feed_url: DownloadResult(url=b.feed_url, error="network_error",
                                       error_message="timed out after 20s"),
            c.feed_url: ok(rss(item("C", "https://gamma.example/1"))),
        })
        summary = self.collect(web, [a, b, c])
        self.assertEqual([s.outcome for s in summary.sources], ["ok", "network_error", "ok"])
        self.assertEqual(summary.status, "completed_with_errors")
        self.assertEqual(summary.exit_code, 1)
        self.assertEqual(self.count("articles"), 2)
        failed = self.fetches()[1]
        self.assertEqual(failed["error_message"], "timed out after 20s")
        self.assertIsNone(failed["http_status"])

    def test_unexpected_downloader_exception_isolated(self):
        a, b = source("alpha"), source("beta")
        web = FakeWeb({
            a.feed_url: RuntimeError("downloader bug"),
            b.feed_url: ok(rss(item("B", "https://beta.example/1"))),
        })
        summary = self.collect(web, [a, b])
        self.assertEqual([s.outcome for s in summary.sources], ["parse_error", "ok"])
        self.assertIn("internal error: RuntimeError: downloader bug", self.fetches()[0]["error_message"])
        self.assertEqual(summary.status, "completed_with_errors")

    def test_storage_failure_rolls_back_only_that_source(self):
        a, b = source("alpha"), source("beta")
        web = FakeWeb({
            a.feed_url: ok(rss(item("A", "https://alpha.example/1"))),
            b.feed_url: ok(rss(item("B1", "https://beta.example/1"), item("B2", "https://beta.example/2"))),
        })
        # A trigger that fails on beta's second article simulates a mid-source DB error.
        self.conn.execute(
            "CREATE TRIGGER fail_beta BEFORE INSERT ON articles"
            " WHEN NEW.normalized_url = 'https://beta.example/2'"
            " BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        self.conn.commit()
        summary = self.collect(web, [a, b])
        self.assertEqual([s.outcome for s in summary.sources], ["ok", "parse_error"])
        self.assertEqual([r["normalized_url"] for r in self.articles()], ["https://alpha.example/1"])
        self.assertEqual(self.count("sightings", "source_id = 'beta'"), 0)
        beta_fetch = self.fetches()[1]
        self.assertIn("simulated failure", beta_fetch["error_message"])
        self.assertIsNone(beta_fetch["etag"])
        self.assertEqual(self.count("fetches", "source_id = 'beta'"), 1)

    def test_run_level_failure_marks_run_failed(self):
        a = source("alpha")
        sync_sources(self.conn, [a], now="2026-10-04T00:00:00Z")

        calls = {"n": 0}

        def clock():
            calls["n"] += 1
            if calls["n"] == 2:  # first call is run start, second is source start
                raise KeyboardInterrupt
            return self.clock()

        with self.assertRaises(KeyboardInterrupt):
            run_collection(self.conn, [a], downloader=FakeWeb(), now=clock, sleep=lambda s: None)
        run = self.conn.execute("SELECT status, error_message FROM runs").fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertIn("KeyboardInterrupt", run["error_message"])


class RobotsTests(CollectorTestCase):
    def test_robots_explicit_block(self):
        a, b = source("alpha"), source("beta")
        web = FakeWeb({
            "https://alpha.example/robots.txt": ok("User-agent: *\nDisallow: /rss\n"),
            b.feed_url: ok(rss(item("B", "https://beta.example/1"))),
        })
        summary = self.collect(web, [a, b])
        self.assertEqual([s.outcome for s in summary.sources], ["robots_disallowed", "ok"])
        self.assertEqual(web.feed_calls(a.feed_url), [])
        self.assertIn("disallowed", self.fetches()[0]["error_message"])
        self.assertEqual(summary.exit_code, 1)

    def test_robots_unreachable_blocks_source(self):
        a = source("alpha")
        web = FakeWeb({
            "https://alpha.example/robots.txt": DownloadResult(
                url="", error="network_error", error_message="ConnectionResetError: reset"),
            a.feed_url: ok(rss(item("A", "https://alpha.example/1"))),
        })
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "robots_disallowed")
        self.assertEqual(web.feed_calls(a.feed_url), [])
        self.assertIn("robots.txt unreachable", self.fetches()[0]["error_message"])

    def test_robots_5xx_blocks_source(self):
        a = source("alpha")
        web = FakeWeb({"https://alpha.example/robots.txt": DownloadResult(url="", status=503),
                       a.feed_url: ok(rss(item("A", "https://alpha.example/1")))})
        self.assertEqual(self.collect(web, [a]).sources[0].outcome, "robots_disallowed")

    def test_robots_404_allows(self):
        a = source("alpha")
        web = FakeWeb({"https://alpha.example/robots.txt": DownloadResult(url="", status=404),
                       a.feed_url: ok(rss(item("A", "https://alpha.example/1")))})
        self.assertEqual(self.collect(web, [a]).sources[0].outcome, "ok")


def moved(location, status=301):
    return DownloadResult(url="", status=status, headers={"location": location})


class RedirectTests(CollectorTestCase):
    def test_same_origin_redirect_succeeds(self):
        a = source("alpha")
        web = FakeWeb({
            a.feed_url: moved("/feeds/main.xml"),
            "https://alpha.example/feeds/main.xml": ok(rss(item("A", "https://alpha.example/1"))),
        })
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "ok")
        fetch = self.fetches()[0]
        self.assertEqual(fetch["request_url"], a.feed_url)
        self.assertEqual(fetch["final_url"], "https://alpha.example/feeds/main.xml")
        self.assertEqual(self.count("articles"), 1)
        self.assertEqual(len([u for u, _ in web.calls if u.endswith("/robots.txt")]), 1)

    def test_cross_origin_redirect_allowed_by_destination_robots(self):
        a = source("alpha")
        web = FakeWeb({
            a.feed_url: moved("https://cdn.example/alpha.xml"),
            "https://cdn.example/robots.txt": ok("User-agent: *\nDisallow: /private\n"),
            "https://cdn.example/alpha.xml": ok(rss(item("A", "https://alpha.example/1"))),
        })
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "ok")
        self.assertEqual(self.fetches()[0]["final_url"], "https://cdn.example/alpha.xml")
        self.assertIn("https://cdn.example/robots.txt", [u for u, _ in web.calls])

    def test_cross_origin_redirect_blocked_by_destination_robots(self):
        a = source("alpha")
        web = FakeWeb({
            a.feed_url: moved("https://cdn.example/alpha.xml"),
            "https://cdn.example/robots.txt": ok("User-agent: *\nDisallow: /\n"),
            "https://cdn.example/alpha.xml": ok(rss(item("A", "https://alpha.example/1"))),
        })
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "robots_disallowed")
        self.assertEqual(summary.exit_code, 1)
        self.assertEqual(web.feed_calls("https://cdn.example/alpha.xml"), [])
        fetch = self.fetches()[0]
        self.assertIn("redirected to https://cdn.example/alpha.xml", fetch["error_message"])
        self.assertEqual(fetch["final_url"], "https://cdn.example/alpha.xml")
        self.assertEqual(fetch["http_status"], 301)
        self.assertEqual(self.count("articles"), 0)

    def test_cross_origin_redirect_blocked_when_destination_robots_unverifiable(self):
        a = source("alpha")
        web = FakeWeb({
            a.feed_url: moved("https://cdn.example/alpha.xml"),
            "https://cdn.example/robots.txt": DownloadResult(
                url="", error="network_error", error_message="timed out after 20s"),
            "https://cdn.example/alpha.xml": ok(rss(item("A", "https://alpha.example/1"))),
        })
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "robots_disallowed")
        self.assertIn("robots.txt unreachable", self.fetches()[0]["error_message"])
        self.assertEqual(web.feed_calls("https://cdn.example/alpha.xml"), [])

    def test_destination_robots_cached_across_sources(self):
        a, b = source("alpha"), source("beta")
        web = FakeWeb({
            a.feed_url: moved("https://cdn.example/alpha.xml"),
            b.feed_url: moved("https://cdn.example/beta.xml"),
            "https://cdn.example/robots.txt": ok(""),
            "https://cdn.example/alpha.xml": ok(rss(item("A", "https://alpha.example/1"))),
            "https://cdn.example/beta.xml": ok(rss(item("B", "https://beta.example/1"))),
        })
        summary = self.collect(web, [a, b])
        self.assertEqual([s.outcome for s in summary.sources], ["ok", "ok"])
        self.assertEqual(len(web.feed_calls("https://cdn.example/robots.txt")), 1)
        self.assertTrue(self.sleeps)  # second request to cdn.example is paced

    def test_redirect_loop_is_http_error(self):
        a = source("alpha")
        web = FakeWeb({
            a.feed_url: moved("https://alpha.example/rss2"),
            "https://alpha.example/rss2": moved(a.feed_url),
        })
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "http_error")
        self.assertIn("redirect loop", self.fetches()[0]["error_message"])

    def test_redirect_limit_is_http_error(self):
        a = source("alpha")
        routes = {a.feed_url: moved("https://alpha.example/r1")}
        routes.update({f"https://alpha.example/r{i}": moved(f"https://alpha.example/r{i + 1}")
                       for i in range(1, 20)})
        web = FakeWeb(routes)
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "http_error")
        self.assertIn("too many redirects", self.fetches()[0]["error_message"])

    def test_conditional_request_through_redirect(self):
        a = source("alpha")
        web = FakeWeb({
            a.feed_url: [moved("https://alpha.example/v2"), moved("https://alpha.example/v2")],
            "https://alpha.example/v2": [
                ok(rss(item("A", "https://alpha.example/1")), headers={"ETag": '"v2"'}),
                DownloadResult(url="", status=304),
            ],
        })
        self.collect(web, [a])
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "not_modified")
        self.assertEqual(web.feed_calls("https://alpha.example/v2")[1], {"If-None-Match": '"v2"'})


class ConditionalRequestTests(CollectorTestCase):
    def test_etag_and_last_modified_sent_and_304_handled(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("A", "https://alpha.example/1")),
               headers={"ETag": '"v1"', "Last-Modified": "Fri, 02 Oct 2026 10:00:00 GMT"}),
            DownloadResult(url=a.feed_url, status=304, headers={}),
            DownloadResult(url=a.feed_url, status=304, headers={"etag": '"v1"'}),
        ]})
        self.collect(web, [a])
        summary = self.collect(web, [a])
        self.assertEqual(summary.sources[0].outcome, "not_modified")
        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.exit_code, 0)
        self.collect(web, [a])

        first, second, third = web.feed_calls(a.feed_url)
        self.assertEqual(first, {})
        self.assertEqual(second, {"If-None-Match": '"v1"',
                                  "If-Modified-Since": "Fri, 02 Oct 2026 10:00:00 GMT"})
        self.assertEqual(third, second)  # validators carried forward through a 304

        fetches = self.fetches()
        self.assertEqual([f["outcome"] for f in fetches], ["ok", "not_modified", "not_modified"])
        self.assertEqual(fetches[1]["http_status"], 304)
        self.assertEqual(fetches[1]["etag"], '"v1"')
        self.assertIsNone(fetches[1]["entry_count"])
        self.assertEqual(self.count("sightings"), 1)

    def test_only_last_modified(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("A", "https://alpha.example/1")),
               headers={"Last-Modified": "Fri, 02 Oct 2026 10:00:00 GMT"}),
            DownloadResult(url=a.feed_url, status=304),
        ]})
        self.collect(web, [a])
        self.collect(web, [a])
        self.assertEqual(web.feed_calls(a.feed_url)[1],
                         {"If-Modified-Since": "Fri, 02 Oct 2026 10:00:00 GMT"})

    def test_only_etag(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("A", "https://alpha.example/1")), headers={"ETag": 'W/"x"'}),
            DownloadResult(url=a.feed_url, status=304),
        ]})
        self.collect(web, [a])
        self.collect(web, [a])
        self.assertEqual(web.feed_calls(a.feed_url)[1], {"If-None-Match": 'W/"x"'})

    def test_failed_fetch_does_not_provide_validators(self):
        a = source("alpha")
        web = FakeWeb({a.feed_url: [
            ok(rss(item("A", "https://alpha.example/1")), headers={"ETag": '"good"'}),
            ok("<html>not a feed</html>", headers={"ETag": '"bad"'}),
            ok(rss(item("A", "https://alpha.example/1"))),
        ]})
        for _ in range(3):
            self.collect(web, [a])
        self.assertEqual(web.feed_calls(a.feed_url)[2], {"If-None-Match": '"good"'})


class OutcomeTests(CollectorTestCase):
    def outcome_for(self, response):
        a = source("alpha")
        summary = self.collect(FakeWeb({a.feed_url: response}), [a])
        return summary.sources[0], self.fetches()[-1]

    def test_http_failure(self):
        outcome, fetch = self.outcome_for(DownloadResult(url="", status=500))
        self.assertEqual(outcome.outcome, "http_error")
        self.assertEqual(fetch["http_status"], 500)
        self.assertEqual(fetch["error_message"], "HTTP 500")

    def test_network_exception(self):
        outcome, fetch = self.outcome_for(DownloadResult(
            url="", error="network_error", error_message="URLError: name resolution failed"))
        self.assertEqual(outcome.outcome, "network_error")
        self.assertIn("name resolution", fetch["error_message"])

    def test_oversized_body(self):
        outcome, fetch = self.outcome_for(DownloadResult(
            url="", status=200, error="too_large", error_message="body exceeds limit of 5242880 bytes"))
        self.assertEqual(outcome.outcome, "too_large")
        self.assertEqual(fetch["http_status"], 200)
        self.assertIsNone(fetch["body_sha256"])

    def test_malformed_but_usable_feed(self):
        outcome, fetch = self.outcome_for(ok((FIXTURES / "malformed.rss").read_bytes()))
        self.assertEqual(outcome.outcome, "ok")
        self.assertTrue(outcome.bozo)
        self.assertEqual(fetch["bozo"], 1)
        self.assertIn("SAXParseException", fetch["bozo_message"])
        self.assertGreaterEqual(self.count("articles"), 1)

    def test_html_non_feed_body(self):
        outcome, fetch = self.outcome_for(ok("<html><body>Please enable JavaScript</body></html>"))
        self.assertEqual(outcome.outcome, "parse_error")
        self.assertIn("not a recognisable RSS/Atom feed", fetch["error_message"])
        self.assertEqual(fetch["entry_count"], 0)
        self.assertIsNotNone(fetch["body_sha256"])

    def test_garbage_body(self):
        outcome, fetch = self.outcome_for(ok(b"\x00\x01garbage"))
        self.assertEqual(outcome.outcome, "parse_error")
        self.assertTrue(fetch["bozo_message"])

    def test_empty_body(self):
        outcome, _ = self.outcome_for(ok(b""))
        self.assertEqual(outcome.outcome, "parse_error")

    def test_valid_empty_feed_is_ok(self):
        outcome, fetch = self.outcome_for(ok((FIXTURES / "empty.rss").read_bytes()))
        self.assertEqual(outcome.outcome, "ok")
        self.assertEqual(fetch["entry_count"], 0)
        self.assertEqual(fetch["new_article_count"], 0)

    def test_unexpected_2xx_or_3xx_status(self):
        outcome, _ = self.outcome_for(DownloadResult(url="", status=302))
        self.assertEqual(outcome.outcome, "http_error")


class RunStatusTests(unittest.TestCase):
    def test_run_status(self):
        self.assertEqual(run_status([]), "completed")
        self.assertEqual(run_status(["ok", "not_modified"]), "completed")
        for bad in ("http_error", "network_error", "parse_error", "robots_disallowed", "too_large"):
            with self.subTest(bad=bad):
                self.assertEqual(run_status(["ok", bad]), "completed_with_errors")

    def test_open_transaction_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.open_database(Path(tmp) / "t.sqlite3")
            try:
                conn.execute("INSERT INTO runs (started_at, status, app_version)"
                             " VALUES ('2026-10-04T00:00:00Z', 'running', 'x')")
                with self.assertRaises(RuntimeError):
                    run_collection(conn, [], downloader=FakeWeb())
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
