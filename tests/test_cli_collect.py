"""Offline tests for `atlas_signal collect`, with a fake downloader."""

import contextlib
import io
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path

from atlas_signal import cli
from atlas_signal.download import DownloadResult


def rss(link):
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
        f"<item><title>x</title><link>{link}</link></item></channel></rss>"
    ).encode()


class FakeWeb:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, **kwargs):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return DownloadResult(url=url, status=404)
        response = self.routes.get(url)
        if response is None:
            return DownloadResult(url=url, status=500)
        return response


CONFIG = """
[[sources]]
id = "alpha"
name = "Alpha"
publisher = "Alpha"
kind = "publisher"
feed_url = "https://alpha.example/rss"

[[sources]]
id = "beta"
name = "Beta"
publisher = "Beta"
kind = "government"
feed_url = "https://beta.example/rss"

[[sources]]
id = "off"
name = "Off"
publisher = "Off"
kind = "publisher"
feed_url = "https://off.example/rss"
enabled = false
"""


class CollectCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.sources = self.tmp / "sources.toml"
        self.sources.write_text(textwrap.dedent(CONFIG), encoding="utf-8")
        self.db = self.tmp / "nested" / "data" / "atlas.sqlite3"
        self.sleeps = []
        self.web = FakeWeb({
            "https://alpha.example/rss": DownloadResult(url="", status=200, body=rss("https://alpha.example/1")),
            "https://beta.example/rss": DownloadResult(url="", status=200, body=rss("https://beta.example/1")),
        })

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["collect", "--db", str(self.db), "--sources", str(self.sources), *args],
                            downloader=self.web, sleep=self.sleeps.append)
        return code, out.getvalue(), err.getvalue()

    def query(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_collect_all_enabled_sources(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertIn("Run 1: completed", out)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertNotIn("off.example", " ".join(self.web.calls))
        self.assertIn("Totals: 2 entries, 2 new articles", out)
        self.assertEqual(self.query("SELECT COUNT(*) FROM articles"), [(2,)])
        # All configured sources are synced, including the disabled one.
        self.assertEqual(self.query("SELECT id, enabled FROM sources ORDER BY id"),
                         [("alpha", 1), ("beta", 1), ("off", 0)])

    def test_creates_database_directory(self):
        self.assertFalse(self.db.parent.exists())
        code, _, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertTrue(self.db.exists())

    def test_source_filter(self):
        code, out, _ = self.run_cli("--source", "beta")
        self.assertEqual(code, 0)
        self.assertNotIn("https://alpha.example/rss", self.web.calls)
        self.assertIn("https://beta.example/rss", self.web.calls)
        self.assertEqual(self.query("SELECT source_id FROM fetches"), [("beta",)])

    def test_repeatable_source_filter(self):
        code, _, _ = self.run_cli("--source", "beta", "--source", "alpha")
        self.assertEqual(code, 0)
        # Config order is kept regardless of option order.
        self.assertEqual(self.query("SELECT source_id FROM fetches ORDER BY id"), [("alpha",), ("beta",)])

    def test_unknown_source_fails_before_any_work(self):
        code, out, err = self.run_cli("--source", "nope")
        self.assertEqual(code, 2)
        self.assertIn("unknown source id(s): nope", err)
        self.assertEqual(self.web.calls, [])
        self.assertFalse(self.db.exists())

    def test_disabled_source_filter_rejected(self):
        code, _, err = self.run_cli("--source", "off")
        self.assertEqual(code, 2)
        self.assertIn("disabled", err)
        self.assertEqual(self.web.calls, [])

    def test_source_error_gives_exit_code_1(self):
        self.web.routes.pop("https://beta.example/rss")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("completed_with_errors", out)
        self.assertIn("http_error", out)
        self.assertIn("1 source(s) with errors", out)

    def test_bad_config_gives_exit_code_2(self):
        self.sources.write_text("not = [valid", encoding="utf-8")
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("not valid TOML", err)
        self.assertEqual(self.web.calls, [])

    def test_unsupported_database_gives_exit_code_2(self):
        self.db.parent.mkdir(parents=True)
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("unsupported database schema version 99", err)
        self.assertEqual(self.web.calls, [])

    def test_second_run_reports_no_new_articles(self):
        self.run_cli()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("Run 2: completed", out)
        self.assertIn("0 new articles", out)

    def test_default_paths(self):
        args = cli.build_parser().parse_args(["collect"])
        self.assertEqual(args.db, "data/atlas_signal.sqlite3")
        self.assertEqual(args.sources, "config/sources.toml")
        self.assertIsNone(args.source_ids)

    def test_collect_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.main(["collect", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("--source ID", out.getvalue())


if __name__ == "__main__":
    unittest.main()
