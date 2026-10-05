"""Tests for the downloader and robots policy.

fetch_url is exercised against a throwaway HTTP server on 127.0.0.1 (no
external network). RobotsPolicy is tested with a fake downloader.
"""

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from atlas_signal.download import (
    MAX_REDIRECTS,
    MAX_ROBOTS_BYTES,
    USER_AGENT,
    DownloadResult,
    RobotsPolicy,
    fetch_url,
    follow_redirects,
)


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # clients that give up early (timeouts, size limits) are expected


class _Handler(BaseHTTPRequestHandler):
    routes = {}
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).requests.append((self.path, dict(self.headers)))
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        route(self)


def _send(handler, status, body=b"", headers=None):
    handler.send_response(status)
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    if "Content-Length" not in (headers or {}):
        handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if body:
        handler.wfile.write(body)


class FetchUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _QuietServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.routes = {}
        _Handler.requests = []

    def route(self, path, func):
        _Handler.routes[path] = func

    def test_success_with_headers_and_user_agent(self):
        self.route("/feed", lambda h: _send(h, 200, b"<rss/>", {
            "ETag": '"abc"', "Last-Modified": "Sat, 03 Oct 2026 10:00:00 GMT",
            "Content-Type": "application/rss+xml"}))
        result = fetch_url(self.base + "/feed")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, b"<rss/>")
        self.assertIsNone(result.error)
        self.assertEqual(result.headers["etag"], '"abc"')
        self.assertEqual(result.headers["last-modified"], "Sat, 03 Oct 2026 10:00:00 GMT")
        self.assertEqual(result.final_url, self.base + "/feed")
        self.assertEqual(_Handler.requests[0][1]["User-Agent"], USER_AGENT)

    def test_conditional_headers_sent_and_304(self):
        self.route("/feed", lambda h: _send(h, 304, headers={"ETag": '"abc"'}))
        result = fetch_url(self.base + "/feed", {
            "If-None-Match": '"abc"', "If-Modified-Since": "Sat, 03 Oct 2026 10:00:00 GMT"})
        self.assertEqual(result.status, 304)
        self.assertIsNone(result.error)
        self.assertIsNone(result.body)
        sent = _Handler.requests[0][1]
        self.assertEqual(sent["If-None-Match"], '"abc"')
        self.assertEqual(sent["If-Modified-Since"], "Sat, 03 Oct 2026 10:00:00 GMT")

    def test_http_errors_are_results_not_exceptions(self):
        self.route("/gone", lambda h: _send(h, 410, b"gone"))
        self.route("/boom", lambda h: _send(h, 503, b"busy"))
        for path, status in (("/gone", 410), ("/boom", 503), ("/missing", 404)):
            with self.subTest(path=path):
                result = fetch_url(self.base + path)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.error)

    def test_redirect_not_followed_by_fetch_url(self):
        self.route("/old", lambda h: _send(h, 301, headers={"Location": "/new"}))
        self.route("/new", lambda h: _send(h, 200, b"moved"))
        result = fetch_url(self.base + "/old")
        self.assertEqual(result.status, 301)
        self.assertIsNone(result.error)
        self.assertEqual(result.headers["location"], "/new")
        self.assertEqual([p for p, _ in _Handler.requests], ["/old"])

    def test_follow_redirects_same_origin_with_real_fetch(self):
        self.route("/old", lambda h: _send(h, 302, headers={"Location": "/new"}))
        self.route("/new", lambda h: _send(h, 200, b"moved"))
        result = follow_redirects(self.base + "/old", {"If-None-Match": '"x"'}, fetch_url)
        self.assertEqual((result.status, result.body), (200, b"moved"))
        self.assertEqual(result.url, self.base + "/old")
        self.assertEqual(result.final_url, self.base + "/new")
        self.assertEqual([p for p, _ in _Handler.requests], ["/old", "/new"])
        self.assertEqual(_Handler.requests[1][1]["If-None-Match"], '"x"')

    def test_too_large_by_content_length(self):
        self.route("/big", lambda h: _send(h, 200, b"x" * 100))
        result = fetch_url(self.base + "/big", max_bytes=50)
        self.assertEqual(result.error, "too_large")
        self.assertIsNone(result.body)
        self.assertIn("Content-Length", result.error_message)

    def test_too_large_without_content_length(self):
        def stream(h):
            h.send_response(200)
            h.send_header("Connection", "close")
            h.end_headers()
            h.wfile.write(b"y" * 200)
        self.route("/stream", stream)
        result = fetch_url(self.base + "/stream", max_bytes=50)
        self.assertEqual(result.error, "too_large")

    def test_body_exactly_at_limit_allowed(self):
        self.route("/edge", lambda h: _send(h, 200, b"z" * 50))
        result = fetch_url(self.base + "/edge", max_bytes=50)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.body), 50)

    def test_timeout_is_network_error(self):
        def slow(h):
            time.sleep(1.5)
            _send(h, 200, b"late")
        self.route("/slow", slow)
        result = fetch_url(self.base + "/slow", timeout=0.3)
        self.assertEqual(result.error, "network_error")
        self.assertIn("timed out", result.error_message)

    def test_connection_refused_is_network_error(self):
        # Port 9 on loopback is almost certainly closed.
        result = fetch_url("http://127.0.0.1:9/feed", timeout=2)
        self.assertEqual(result.error, "network_error")
        self.assertIsNone(result.status)

    def test_invalid_url_is_network_error(self):
        result = fetch_url("not a url")
        self.assertEqual(result.error, "network_error")


class FakeDownloader:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {}), kwargs))
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            return DownloadResult(url=url, status=404)
        return response


def robots(body, status=200):
    return DownloadResult(url="", status=status, body=body.encode())


class RobotsPolicyTests(unittest.TestCase):
    def test_allowed_and_disallowed_paths(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": robots(
            "User-agent: *\nDisallow: /private/\n")})
        policy = RobotsPolicy(fake)
        self.assertTrue(policy.check("https://ex.example/feed.xml").allowed)
        decision = policy.check("https://ex.example/private/feed.xml")
        self.assertFalse(decision.allowed)
        self.assertIn("disallowed", decision.reason)

    def test_rules_for_our_agent(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": robots(
            "User-agent: AtlasSignal\nDisallow: /\n\nUser-agent: *\nAllow: /\n")})
        self.assertFalse(RobotsPolicy(fake).check("https://ex.example/feed").allowed)

    def test_rules_for_other_agents_do_not_apply(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": robots(
            "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")})
        self.assertTrue(RobotsPolicy(fake).check("https://ex.example/feed").allowed)

    def test_query_string_considered(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": robots(
            "User-agent: *\nDisallow: /rss/search\n")})
        policy = RobotsPolicy(fake)
        self.assertFalse(policy.check("https://ex.example/rss/search?q=x").allowed)
        self.assertTrue(policy.check("https://ex.example/rss/topics?q=x").allowed)

    def test_4xx_means_allowed(self):
        for status in (401, 403, 404, 410):
            with self.subTest(status=status):
                fake = FakeDownloader({"https://ex.example/robots.txt": robots("", status)})
                decision = RobotsPolicy(fake).check("https://ex.example/feed")
                self.assertTrue(decision.allowed)
                self.assertIn(f"HTTP {status}", decision.reason)

    def test_5xx_means_disallowed(self):
        for status in (500, 503):
            with self.subTest(status=status):
                fake = FakeDownloader({"https://ex.example/robots.txt": robots("", status)})
                decision = RobotsPolicy(fake).check("https://ex.example/feed")
                self.assertFalse(decision.allowed)
                self.assertIn("unreachable", decision.reason)

    def test_network_failure_means_disallowed(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": DownloadResult(
            url="", error="network_error", error_message="timed out after 20s")})
        decision = RobotsPolicy(fake).check("https://ex.example/feed")
        self.assertFalse(decision.allowed)
        self.assertIn("timed out", decision.reason)

    def test_oversized_robots_means_disallowed(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": DownloadResult(
            url="", status=200, error="too_large", error_message="too big")})
        self.assertFalse(RobotsPolicy(fake).check("https://ex.example/feed").allowed)

    def test_robots_requested_with_size_limit_and_no_conditional_headers(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": robots("")})
        RobotsPolicy(fake).check("https://ex.example/feed")
        url, headers, kwargs = fake.calls[0]
        self.assertEqual(url, "https://ex.example/robots.txt")
        self.assertEqual(headers, {})
        self.assertEqual(kwargs["max_bytes"], MAX_ROBOTS_BYTES)

    def test_cached_per_origin(self):
        fake = FakeDownloader({
            "https://ex.example/robots.txt": robots("User-agent: *\nDisallow:\n"),
            "https://other.example/robots.txt": robots(""),
            "http://ex.example/robots.txt": robots(""),
        })
        policy = RobotsPolicy(fake)
        policy.check("https://ex.example/a")
        policy.check("https://EX.example/b")
        policy.check("https://other.example/a")
        policy.check("http://ex.example/a")
        self.assertEqual(
            [c[0] for c in fake.calls],
            ["https://ex.example/robots.txt", "https://other.example/robots.txt",
             "http://ex.example/robots.txt"],
        )

    def test_failure_also_cached(self):
        fake = FakeDownloader({"https://ex.example/robots.txt": robots("", 503)})
        policy = RobotsPolicy(fake)
        policy.check("https://ex.example/a")
        policy.check("https://ex.example/b")
        self.assertEqual(len(fake.calls), 1)

    def test_before_request_hook_called(self):
        seen = []
        fake = FakeDownloader({"https://ex.example/robots.txt": robots("")})
        RobotsPolicy(fake, before_request=seen.append).check("https://ex.example/feed")
        self.assertEqual(seen, ["https://ex.example/robots.txt"])


def redirect(location, status=301):
    return DownloadResult(url="", status=status, headers={"location": location})


def page(body=b"<rss/>", status=200):
    return DownloadResult(url="", status=status, body=body)


class FollowRedirectsTests(unittest.TestCase):
    def follow(self, routes, url="https://a.example/feed"):
        fake = FakeDownloader(routes)
        result = follow_redirects(url, {"If-None-Match": '"e"'}, fake, robots=RobotsPolicy(fake))
        return result, fake

    def feed_urls(self, fake):
        return [c[0] for c in fake.calls if not c[0].endswith("/robots.txt")]

    def test_no_redirect(self):
        result, _ = self.follow({"https://a.example/feed": page()})
        self.assertEqual((result.status, result.body, result.final_url),
                         (200, b"<rss/>", "https://a.example/feed"))

    def test_same_origin_redirect_uses_cached_policy(self):
        result, fake = self.follow({
            "https://a.example/robots.txt": robots("User-agent: *\nDisallow:\n"),
            "https://a.example/feed": redirect("/new-feed"),
            "https://a.example/new-feed": page(),
        })
        self.assertEqual(result.status, 200)
        self.assertEqual(result.final_url, "https://a.example/new-feed")
        # The caller checks the original URL; the policy is consulted for the
        # target and robots.txt is requested once for the origin.
        self.assertEqual([c[0] for c in fake.calls],
                         ["https://a.example/feed", "https://a.example/robots.txt",
                          "https://a.example/new-feed"])

    def test_same_origin_redirect_to_disallowed_path_blocked(self):
        result, fake = self.follow({
            "https://a.example/robots.txt": robots("User-agent: *\nDisallow: /private\n"),
            "https://a.example/feed": redirect("/private/feed"),
            "https://a.example/private/feed": page(),
        })
        self.assertEqual(result.error, "robots_disallowed")
        self.assertNotIn("https://a.example/private/feed", self.feed_urls(fake))

    def test_cross_origin_redirect_allowed(self):
        result, fake = self.follow({
            "https://a.example/feed": redirect("https://b.example/feed"),
            "https://b.example/robots.txt": robots("User-agent: *\nDisallow: /private\n"),
            "https://b.example/feed": page(b"from b"),
        })
        self.assertEqual((result.status, result.body), (200, b"from b"))
        self.assertEqual(result.url, "https://a.example/feed")
        self.assertEqual(result.final_url, "https://b.example/feed")
        self.assertIn("https://b.example/robots.txt", [c[0] for c in fake.calls])
        self.assertEqual(fake.calls[-1][1], {"If-None-Match": '"e"'})

    def test_cross_origin_redirect_blocked_by_destination_robots(self):
        result, fake = self.follow({
            "https://a.example/feed": redirect("https://b.example/feed"),
            "https://b.example/robots.txt": robots("User-agent: *\nDisallow: /\n"),
            "https://b.example/feed": page(b"must not be fetched"),
        })
        self.assertEqual(result.error, "robots_disallowed")
        self.assertIn("redirected to https://b.example/feed", result.error_message)
        self.assertIn("disallowed by https://b.example/robots.txt", result.error_message)
        self.assertEqual(result.final_url, "https://b.example/feed")
        self.assertEqual(result.status, 301)
        self.assertNotIn("https://b.example/feed", self.feed_urls(fake))

    def test_cross_origin_redirect_blocked_when_destination_robots_unverifiable(self):
        for robots_response in (
            DownloadResult(url="", status=503),
            DownloadResult(url="", error="network_error", error_message="timed out after 20s"),
        ):
            with self.subTest(robots_response=robots_response):
                result, fake = self.follow({
                    "https://a.example/feed": redirect("https://b.example/feed"),
                    "https://b.example/robots.txt": robots_response,
                    "https://b.example/feed": page(),
                })
                self.assertEqual(result.error, "robots_disallowed")
                self.assertIn("robots.txt unreachable", result.error_message)
                self.assertNotIn("https://b.example/feed", self.feed_urls(fake))

    def test_scheme_change_is_a_different_origin(self):
        result, fake = self.follow({
            "http://a.example/feed": redirect("https://a.example/feed"),
            "https://a.example/robots.txt": robots("User-agent: *\nDisallow: /\n"),
        }, url="http://a.example/feed")
        self.assertEqual(result.error, "robots_disallowed")
        self.assertIn("https://a.example/robots.txt", [c[0] for c in fake.calls])

    def test_destination_robots_cached(self):
        fake = FakeDownloader({
            "https://a.example/one": redirect("https://b.example/one"),
            "https://a.example/two": redirect("https://b.example/two"),
            "https://b.example/robots.txt": robots(""),
            "https://b.example/one": page(),
            "https://b.example/two": page(),
        })
        policy = RobotsPolicy(fake)
        follow_redirects("https://a.example/one", {}, fake, robots=policy)
        follow_redirects("https://a.example/two", {}, fake, robots=policy)
        robots_calls = [c[0] for c in fake.calls if c[0].endswith("/robots.txt")]
        self.assertEqual(robots_calls, ["https://b.example/robots.txt"])

    def test_redirect_loop(self):
        result, fake = self.follow({
            "https://a.example/feed": redirect("/other"),
            "https://a.example/other": redirect("/feed"),
        })
        self.assertEqual(result.error, "http_error")
        self.assertIn("redirect loop", result.error_message)
        self.assertEqual(self.feed_urls(fake), ["https://a.example/feed", "https://a.example/other"])

    def test_self_redirect_is_a_loop(self):
        result, _ = self.follow({"https://a.example/feed": redirect("https://a.example/feed")})
        self.assertEqual(result.error, "http_error")
        self.assertIn("redirect loop", result.error_message)

    def test_redirect_limit(self):
        routes = {f"https://a.example/{i}": redirect(f"/{i + 1}") for i in range(10)}
        result, fake = self.follow(routes, url="https://a.example/0")
        self.assertEqual(result.error, "http_error")
        self.assertIn(f"too many redirects (limit {MAX_REDIRECTS})", result.error_message)
        self.assertEqual(len(self.feed_urls(fake)), MAX_REDIRECTS + 1)

    def test_exactly_max_redirects_allowed(self):
        routes = {f"https://a.example/{i}": redirect(f"/{i + 1}") for i in range(MAX_REDIRECTS)}
        routes[f"https://a.example/{MAX_REDIRECTS}"] = page()
        result, _ = self.follow(routes, url="https://a.example/0")
        self.assertEqual(result.status, 200)

    def test_missing_or_bad_location(self):
        for headers in ({}, {"location": "   "}, {"location": "ftp://a.example/feed"}):
            with self.subTest(headers=headers):
                result, _ = self.follow({
                    "https://a.example/feed": DownloadResult(url="", status=302, headers=headers)})
                self.assertEqual(result.error, "http_error")
                self.assertIn("without a usable Location", result.error_message)

    def test_non_redirect_3xx_returned_as_is(self):
        result, _ = self.follow({"https://a.example/feed": DownloadResult(url="", status=304)})
        self.assertEqual(result.status, 304)
        self.assertIsNone(result.error)

    def test_before_request_called_for_every_hop(self):
        seen = []
        fake = FakeDownloader({
            "https://a.example/feed": redirect("https://b.example/feed"),
            "https://b.example/feed": page(),
        })
        follow_redirects("https://a.example/feed", {}, fake,
                         robots=RobotsPolicy(fake, before_request=seen.append),
                         before_request=seen.append)
        self.assertEqual(seen, ["https://a.example/feed", "https://b.example/robots.txt",
                                "https://b.example/feed"])

    def test_size_limit_passed_to_every_hop(self):
        fake = FakeDownloader({
            "https://a.example/feed": redirect("/new"),
            "https://a.example/new": page(),
        })
        follow_redirects("https://a.example/feed", {}, fake, max_bytes=1234)
        self.assertEqual([c[2]["max_bytes"] for c in fake.calls], [1234, 1234])

    def test_robots_txt_redirect_followed(self):
        fake = FakeDownloader({
            "http://a.example/robots.txt": redirect("https://www.a.example/robots.txt"),
            "https://www.a.example/robots.txt": robots("User-agent: *\nDisallow: /private\n"),
        })
        policy = RobotsPolicy(fake)
        self.assertTrue(policy.check("http://a.example/feed").allowed)
        self.assertFalse(policy.check("http://a.example/private/x").allowed)


class CrossOriginLocalServerTest(unittest.TestCase):
    """End-to-end with the real fetch_url: two local servers are two origins."""

    def setUp(self):
        self.servers = []
        for _ in range(2):
            handler = type("H", (_Handler,), {"routes": {}, "requests": []})
            server = _QuietServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers.append((server, handler, f"http://127.0.0.1:{server.server_address[1]}"))
        self.addCleanup(self._stop)

    def _stop(self):
        for server, _, _ in self.servers:
            server.shutdown()
            server.server_close()

    def test_cross_origin_blocked_then_allowed(self):
        (_, a, a_base), (_, b, b_base) = self.servers
        a.routes["/robots.txt"] = lambda h: _send(h, 404)
        a.routes["/feed"] = lambda h: _send(h, 301, headers={"Location": b_base + "/feed"})
        b.routes["/feed"] = lambda h: _send(h, 200, b"<rss/>")

        b.routes["/robots.txt"] = lambda h: _send(h, 200, b"User-agent: *\nDisallow: /\n")
        blocked = follow_redirects(a_base + "/feed", {}, fetch_url, robots=RobotsPolicy(fetch_url))
        self.assertEqual(blocked.error, "robots_disallowed")
        self.assertNotIn("/feed", [p for p, _ in b.requests])

        b.routes["/robots.txt"] = lambda h: _send(h, 200, b"User-agent: *\nAllow: /\n")
        allowed = follow_redirects(a_base + "/feed", {}, fetch_url, robots=RobotsPolicy(fetch_url))
        self.assertEqual((allowed.status, allowed.body), (200, b"<rss/>"))
        self.assertEqual(allowed.final_url, b_base + "/feed")
        self.assertEqual([p for p, _ in b.requests], ["/robots.txt", "/robots.txt", "/feed"])


if __name__ == "__main__":
    unittest.main()
