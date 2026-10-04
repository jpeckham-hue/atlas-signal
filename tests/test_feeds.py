import json
import unittest
from pathlib import Path

import feedparser

from atlas_signal import feeds
from atlas_signal.feeds import (
    EntryMappingError,
    FeedEntry,
    clean_text,
    date_status,
    entry_fingerprint,
    html_to_text,
    map_entry,
    parse_feed,
    resolve_date_status,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_bytes()


def parse_fixture(name):
    return parse_feed(fixture(name))


def rss(items, language=None):
    lang = f"<language>{language}</language>" if language else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>T</title>'
        f"{lang}{items}</channel></rss>"
    ).encode("utf-8")


class BbcStyleTests(unittest.TestCase):
    def setUp(self):
        self.feed = parse_fixture("bbc_style.rss")
        self.entry = self.feed.entries[0]

    def test_rss_parsed(self):
        self.assertEqual(self.feed.version, "rss20")
        self.assertFalse(self.feed.bozo)
        self.assertIsNone(self.feed.bozo_message)
        self.assertEqual(len(self.feed.entries), 2)
        self.assertEqual(self.feed.errors, ())
        self.assertEqual(self.feed.entry_count, 2)

    def test_guid_differs_from_url(self):
        self.assertEqual(self.entry.entry_guid, "https://www.broadcaster.example/news/articles/abc123#0")
        self.assertEqual(
            self.entry.url_as_seen,
            "https://www.broadcaster.example/news/articles/abc123?at_medium=RSS&at_campaign=rss",
        )

    def test_tracking_params_removed_but_url_as_seen_kept(self):
        self.assertEqual(self.entry.normalized_url, "https://www.broadcaster.example/news/articles/abc123")
        self.assertIn("at_medium=RSS", self.entry.url_as_seen)

    def test_fields(self):
        self.assertEqual(self.entry.title, "Widget makers report record quarter")
        self.assertEqual(self.entry.summary, "Output rose sharply as demand for widgets grew.")
        self.assertIsNone(self.entry.author_raw)
        self.assertIsNone(self.entry.author)
        self.assertEqual(self.entry.categories, ())
        self.assertEqual(self.entry.language, "en-gb")
        self.assertEqual(self.feed.language, "en-gb")

    def test_dates(self):
        self.assertEqual(self.entry.published_raw, "Sat, 03 Oct 2026 04:38:31 GMT")
        self.assertEqual(self.entry.published_at, "2026-10-03T04:38:31Z")
        self.assertIsNone(self.entry.updated_raw)
        self.assertIsNone(self.entry.updated_at)
        self.assertEqual(self.entry.date_status, "ok")

    def test_entry_indexes(self):
        self.assertEqual([e.index for e in self.feed.entries], [0, 1])


class CbcStyleTests(unittest.TestCase):
    def setUp(self):
        self.feed = parse_fixture("cbc_style.rss")
        self.entry = self.feed.entries[0]

    def test_non_url_guid_kept_and_link_used(self):
        self.assertEqual(self.entry.entry_guid, "1.234567")
        self.assertEqual(
            self.entry.normalized_url,
            "https://www.network.example/news/business/grocery-code-1.234567",
        )
        self.assertTrue(self.entry.url_as_seen.endswith("?cmp=rss"))

    def test_title_entities_and_whitespace(self):
        self.assertEqual(self.entry.title_raw, "Grocers &amp; suppliers   agree on   new code")
        self.assertEqual(self.entry.title, "Grocers & suppliers agree on new code")

    def test_author_cleaned_raw_kept(self):
        self.assertEqual(self.entry.author_raw, "Alex   Example,  Sam Sample")
        self.assertEqual(self.entry.author, "Alex Example, Sam Sample")

    def test_html_summary_to_plain_text(self):
        self.assertIn("<img", self.entry.summary_raw)
        self.assertIn("&mdash;", self.entry.summary_raw)
        self.assertEqual(
            self.entry.summary,
            "Retailers and suppliers signed a voluntary code — the first of its kind. "
            "It takes effect in January.",
        )

    def test_categories(self):
        self.assertEqual(self.entry.categories, ("Business", "News/Business"))

    def test_named_zone_date_converted_to_utc(self):
        self.assertEqual(self.entry.published_raw, "Fri, 02 Oct 2026 21:15:00 EDT")
        self.assertEqual(self.entry.published_at, "2026-10-03T01:15:00Z")

    def test_language(self):
        self.assertEqual(self.feed.language, "en-ca")


class EcStyleTests(unittest.TestCase):
    def test_entry(self):
        feed = parse_fixture("ec_style.rss")
        (entry,) = feed.entries
        self.assertEqual(entry.entry_guid, entry.url_as_seen)
        self.assertEqual(entry.normalized_url, "https://press.commission.example/detail/en/statement_26_0001")
        self.assertEqual(entry.categories, ("POLICY_AREA=TRADE",))
        self.assertTrue(entry.summary.endswith("next steps for..."))
        self.assertEqual(entry.published_at, "2026-10-02T15:38:58Z")
        self.assertEqual(entry.date_status, "ok")
        self.assertEqual(entry.language, "en")


class GcStyleAtomTests(unittest.TestCase):
    def setUp(self):
        self.feed = parse_fixture("gc_style.atom")
        self.entry = self.feed.entries[0]

    def test_atom_parsed(self):
        self.assertEqual(self.feed.version, "atom10")
        self.assertFalse(self.feed.bozo)
        self.assertEqual(len(self.feed.entries), 2)

    def test_department_author_and_categories(self):
        self.assertEqual(self.entry.author, "Department of Example Agriculture")
        self.assertEqual(self.entry.categories, ("news releases", "agriculture"))

    def test_updated_only_with_offset_converted_to_utc(self):
        self.assertIsNone(self.entry.published_raw)
        self.assertIsNone(self.entry.published_at)
        self.assertEqual(self.entry.updated_raw, "2026-10-03T09:02:48-04:00")
        self.assertEqual(self.entry.updated_at, "2026-10-03T13:02:48Z")
        self.assertEqual(self.entry.date_status, "updated_only")

    def test_publication_date_not_invented(self):
        for entry in self.feed.entries:
            self.assertIsNone(entry.published_at)

    def test_link_and_language(self):
        self.assertEqual(
            self.entry.normalized_url,
            "https://www.government.example/en/agriculture/news/2026/10/example-food-hubs.html",
        )
        self.assertEqual(self.feed.language, "en-ca")
        self.assertEqual(self.entry.language, "en-ca")


class UrlSelectionTests(unittest.TestCase):
    def setUp(self):
        self.feed = parse_fixture("no_url.rss")

    def test_good_entries_survive_bad_ones(self):
        self.assertEqual([e.index for e in self.feed.entries], [0, 3, 5])
        self.assertEqual([e.index for e in self.feed.errors], [1, 2, 4])
        self.assertEqual(self.feed.entry_count, 6)

    def test_http_guid_used_as_url_fallback(self):
        entry = self.feed.entries[1]
        self.assertEqual(entry.url_as_seen, "https://edge.example/from-guid?utm_source=rss")
        self.assertEqual(entry.normalized_url, "https://edge.example/from-guid")

    def test_missing_url_becomes_entry_error(self):
        by_index = {e.index: e for e in self.feed.errors}
        self.assertEqual(by_index[1].entry_guid, "item-0002")
        self.assertIsNone(by_index[1].link_raw)
        self.assertIn("no link", by_index[1].error)
        self.assertIsNone(by_index[2].entry_guid)
        self.assertIn("no link", by_index[2].error)
        for error in self.feed.errors:
            self.assertIsNotNone(error.raw_entry_json)
            json.loads(error.raw_entry_json)

    def test_relative_link_becomes_entry_error(self):
        error = {e.index: e for e in self.feed.errors}[4]
        self.assertEqual(error.link_raw, "/relative/path")
        self.assertIn("unusable URL", error.error)

    def test_http_and_case_normalized(self):
        entry = self.feed.entries[2]
        self.assertEqual(entry.url_as_seen, "http://EDGE.example/other#comments")
        self.assertEqual(entry.normalized_url, "https://edge.example/other")

    def test_atom_entry_without_link_uses_http_id(self):
        body = (
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>T</title>'
            b"<entry><title>x</title><id>https://ex.example/a?utm_source=x</id>"
            b"<updated>2026-10-03T09:02:48Z</updated></entry>"
            b"<entry><title>y</title><id>urn:uuid:1234</id></entry></feed>"
        )
        feed = parse_feed(body)
        self.assertEqual([e.normalized_url for e in feed.entries], ["https://ex.example/a"])
        self.assertEqual(len(feed.errors), 1)
        self.assertEqual(feed.errors[0].entry_guid, "urn:uuid:1234")


class MalformedAndEmptyTests(unittest.TestCase):
    def test_malformed_feed_returns_recoverable_entries(self):
        feed = parse_fixture("malformed.rss")
        self.assertTrue(feed.bozo)
        self.assertIn("SAXParseException", feed.bozo_message)
        self.assertGreaterEqual(len(feed.entries), 1)
        self.assertEqual(feed.entries[0].normalized_url, "https://broken.example/recoverable")

    def test_empty_feed(self):
        feed = parse_fixture("empty.rss")
        self.assertEqual(feed.entries, ())
        self.assertEqual(feed.errors, ())
        self.assertFalse(feed.bozo)
        self.assertEqual(feed.version, "rss20")
        self.assertEqual(feed.entry_count, 0)

    def test_empty_body(self):
        feed = parse_feed(b"")
        self.assertEqual((feed.entries, feed.errors), ((), ()))
        self.assertEqual(feed.version, "")

    def test_non_feed_bodies_do_not_raise(self):
        for body in [b"<html><body>Not a feed</body></html>", b"\x00\x01garbage", b"{}"]:
            with self.subTest(body=body):
                feed = parse_feed(body)
                self.assertEqual(feed.entries, ())
                self.assertEqual(feed.version, "")

    def test_bozo_message_present_for_garbage(self):
        feed = parse_feed(b"\x00\x01garbage")
        self.assertTrue(feed.bozo)
        self.assertTrue(feed.bozo_message)

    def test_unexpected_mapping_failure_is_isolated(self):
        original = feeds.html_to_text

        def explode(value):
            if value and "boom" in value:
                raise RuntimeError("summary exploded")
            return original(value)

        body = rss(
            "<item><title>a</title><link>https://ex.example/a</link><description>boom</description></item>"
            "<item><title>b</title><link>https://ex.example/b</link></item>"
        )
        feeds.html_to_text = explode
        try:
            feed = parse_feed(body)
        finally:
            feeds.html_to_text = original
        self.assertEqual([e.index for e in feed.entries], [1])
        self.assertEqual(feed.errors[0].index, 0)
        self.assertIn("summary exploded", feed.errors[0].error)
        self.assertEqual(feed.errors[0].link_raw, "https://ex.example/a")


class DateTests(unittest.TestCase):
    def entry(self, dates):
        feed = parse_feed(rss(f"<item><title>t</title><link>https://ex.example/a</link>{dates}</item>"))
        return feed.entries[0]

    def test_ok(self):
        entry = self.entry("<pubDate>Fri, 02 Oct 2026 10:00:00 +0000</pubDate>")
        self.assertEqual((entry.date_status, entry.published_at), ("ok", "2026-10-02T10:00:00Z"))

    def test_offset_converted_to_utc(self):
        entry = self.entry("<pubDate>Sat, 03 Oct 2026 09:02:48 -0400</pubDate>")
        self.assertEqual(entry.published_at, "2026-10-03T13:02:48Z")
        self.assertEqual(entry.published_raw, "Sat, 03 Oct 2026 09:02:48 -0400")

    def test_rss_pubdate_does_not_fill_updated(self):
        # feedparser's .get("updated") falls back to published; we must not.
        entry = self.entry("<pubDate>Fri, 02 Oct 2026 10:00:00 GMT</pubDate>")
        self.assertIsNone(entry.updated_raw)
        self.assertIsNone(entry.updated_at)

    def test_unparseable_published(self):
        entry = self.entry("<pubDate>Someday soon</pubDate>")
        self.assertEqual(entry.published_raw, "Someday soon")
        self.assertIsNone(entry.published_at)
        self.assertEqual(entry.date_status, "unparseable")

    def test_missing(self):
        entry = self.entry("")
        self.assertEqual(entry.date_status, "missing")
        self.assertIsNone(entry.published_raw)

    def test_empty_date_element_is_missing(self):
        self.assertEqual(self.entry("<pubDate></pubDate>").date_status, "missing")

    def test_date_status_function(self):
        cases = [
            (("raw", "2026-10-02T10:00:00Z", None, None), "ok"),
            (("raw", "2026-10-02T10:00:00Z", "raw", "2026-10-03T10:00:00Z"), "ok"),
            ((None, None, "raw", "2026-10-03T10:00:00Z"), "updated_only"),
            (("bad", None, None, None), "unparseable"),
            (("bad", None, "raw", "2026-10-03T10:00:00Z"), "unparseable"),
            ((None, None, "bad", None), "unparseable"),
            ((None, None, None, None), "missing"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(date_status(*args), expected)

    def test_future_detection(self):
        seen = "2026-10-04T12:00:00Z"
        self.assertEqual(resolve_date_status("ok", "2026-10-05T12:00:01Z", seen), "future")
        self.assertEqual(resolve_date_status("ok", "2026-10-05T12:00:00Z", seen), "ok")
        self.assertEqual(resolve_date_status("ok", "2026-10-04T18:00:00Z", seen), "ok")
        self.assertEqual(resolve_date_status("ok", "2026-09-01T00:00:00Z", seen), "ok")
        for status in ("updated_only", "unparseable", "missing"):
            with self.subTest(status=status):
                self.assertEqual(resolve_date_status(status, None, seen), status)


class TextTests(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(clean_text("  A &amp; B\n\t C  "), "A & B C")
        self.assertEqual(clean_text("Caf&eacute; &#8212; &#x2014;"), "Café — —")
        self.assertIsNone(clean_text(None))
        self.assertIsNone(clean_text("   "))

    def test_clean_text_keeps_angle_brackets(self):
        self.assertEqual(clean_text("Profits <b>up</b>"), "Profits <b>up</b>")

    def test_html_to_text(self):
        self.assertEqual(html_to_text("<p>One</p><p>Two<br/>Three</p>"), "One Two Three")
        self.assertEqual(html_to_text("A&nbsp;&amp;&nbsp;B"), "A & B")  # nbsp collapses to a space
        self.assertEqual(html_to_text("<b>bold</b> and <i>it</i>"), "bold and it")
        self.assertEqual(html_to_text("x<script>alert(1)</script>y<style>p{}</style>z"), "xyz")
        self.assertEqual(html_to_text("<img src='a.jpg' alt='photo'/>Text"), "Text")
        self.assertEqual(html_to_text("<ul><li>a</li><li>b</li></ul>"), "a b")
        self.assertEqual(html_to_text("Profits < expected"), "Profits < expected")
        self.assertIsNone(html_to_text(None))
        self.assertIsNone(html_to_text("<img src='a.jpg'/>"))

    def test_plain_text_summary_unchanged(self):
        self.assertEqual(html_to_text("Just plain text."), "Just plain text.")


class RawEvidenceTests(unittest.TestCase):
    def test_raw_json_is_deterministic_and_valid(self):
        first = parse_fixture("cbc_style.rss").entries[0].raw_entry_json
        second = parse_fixture("cbc_style.rss").entries[0].raw_entry_json
        self.assertEqual(first, second)
        data = json.loads(first)
        self.assertEqual(data["link"], "https://www.network.example/news/business/grocery-code-1.234567?cmp=rss")
        self.assertEqual(data["published_parsed"][:6], [2026, 10, 3, 1, 15, 0])
        self.assertEqual(list(data), sorted(data))

    def test_fingerprint_deterministic(self):
        a = parse_fixture("bbc_style.rss").entries[0]
        b = parse_fixture("bbc_style.rss").entries[0]
        self.assertEqual(a.entry_sha256, b.entry_sha256)
        self.assertRegex(a.entry_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            a.entry_sha256,
            entry_fingerprint(a.entry_guid, a.url_as_seen, a.title_raw, a.summary_raw,
                              a.published_raw, a.updated_raw),
        )

    def test_fingerprint_changes_with_each_input(self):
        base = ["guid", "https://ex.example/a", "Title", "Summary", "Mon", "Tue"]
        original = entry_fingerprint(*base)
        for position in range(len(base)):
            changed = list(base)
            changed[position] = (changed[position] or "") + "!"
            with self.subTest(position=position):
                self.assertNotEqual(entry_fingerprint(*changed), original)
        moved = entry_fingerprint("guid", "https://ex.example/a", "Summary", "Title", "Mon", "Tue")
        self.assertNotEqual(moved, original)
        self.assertNotEqual(entry_fingerprint(None, *base[1:]), entry_fingerprint("", *base[1:]))

    def test_edited_entry_changes_fingerprint(self):
        item = "<item><title>{}</title><link>https://ex.example/a</link><guid>g1</guid></item>"
        before = parse_feed(rss(item.format("Original headline"))).entries[0]
        after = parse_feed(rss(item.format("Corrected headline"))).entries[0]
        self.assertEqual(before.normalized_url, after.normalized_url)
        self.assertNotEqual(before.entry_sha256, after.entry_sha256)


class MapEntryTests(unittest.TestCase):
    def test_map_entry_direct(self):
        raw = feedparser.parse(rss("<item><title>t</title><link>https://ex.example/a</link></item>")).entries[0]
        entry = map_entry(raw, index=7, language="fr")
        self.assertIsInstance(entry, FeedEntry)
        self.assertEqual(entry.index, 7)
        self.assertEqual(entry.language, "fr")

    def test_map_entry_raises_for_missing_url(self):
        raw = feedparser.parse(rss("<item><title>t</title></item>")).entries[0]
        with self.assertRaises(EntryMappingError):
            map_entry(raw, index=0)

    def test_duplicate_categories_collapsed(self):
        feed = parse_feed(rss(
            "<item><title>t</title><link>https://ex.example/a</link>"
            "<category>Biz</category><category> Biz </category><category>Tech</category></item>"
        ))
        self.assertEqual(feed.entries[0].categories, ("Biz", "Tech"))


if __name__ == "__main__":
    unittest.main()
