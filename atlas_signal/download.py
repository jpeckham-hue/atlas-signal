"""HTTP downloading and robots.txt checks for feed collection (standard library only).

``fetch_url`` is the real downloader. It makes exactly one request and never
follows redirects itself; ``follow_redirects`` does that explicitly so that a
redirect to another origin can be checked against that origin's robots.txt.

The collector accepts any callable with ``fetch_url``'s signature, so tests can
inject a fake and never touch the network. Downloaders never raise for HTTP or
network problems; they return a ``DownloadResult`` describing what happened.
"""

import http.client
import socket
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from urllib.parse import urljoin, urlsplit

USER_AGENT = "AtlasSignal/0.1 (+https://github.com/jpeckham-hue/atlas-signal)"
TIMEOUT_SECONDS = 20
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class DownloadResult:
    """What happened when a URL was requested.

    ``error`` is None for any HTTP response that was received in full
    (including 3xx, 304 and 4xx/5xx). Otherwise it is one of:
    - "network_error": no usable response arrived;
    - "too_large": the body exceeded the size limit;
    - "robots_disallowed" / "http_error": set only by ``follow_redirects``,
      when a redirect target is blocked by robots.txt or the redirect chain
      is unusable (loop, too long, bad Location).
    These values match the ``fetches.outcome`` vocabulary.
    """

    url: str
    final_url: str | None = None
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)  # lower-case names
    body: bytes | None = None
    error: str | None = None
    error_message: str | None = None


Downloader = Callable[..., DownloadResult]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises HTTPError, returned below as a 3xx result


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch_url(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    max_bytes: int = MAX_FEED_BYTES,
    timeout: float = TIMEOUT_SECONDS,
) -> DownloadResult:
    """GET ``url`` once (no retries, no redirects) with the Atlas Signal User-Agent.

    Extra request headers (e.g. If-None-Match) can be passed in ``headers``.
    A redirect comes back as a 3xx result with its ``location`` header.
    """
    request_headers = {"User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    try:
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        with _OPENER.open(request, timeout=timeout) as response:
            return _read_response(url, response, response.status, max_bytes)
    except urllib.error.HTTPError as err:
        # urllib raises for 3xx (redirects are not followed), 304 and 4xx/5xx;
        # these are still HTTP responses.
        with err:
            return DownloadResult(
                url=url,
                final_url=url,
                status=err.code,
                headers=_header_dict(err.headers),
                body=None,
            )
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as err:
        reason = getattr(err, "reason", None) or err
        if isinstance(reason, (TimeoutError, socket.timeout)):
            message = f"timed out after {timeout:g}s"
        else:
            message = f"{type(reason).__name__}: {reason}"
        return DownloadResult(url=url, error="network_error", error_message=message)


def _read_response(url, response, status, max_bytes) -> DownloadResult:
    headers = _header_dict(response.headers)
    final_url = response.geturl() or url
    declared = headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        return DownloadResult(
            url=url, final_url=final_url, status=status, headers=headers,
            error="too_large",
            error_message=f"Content-Length {declared} exceeds limit of {max_bytes} bytes",
        )
    chunks, total = [], 0
    while True:
        chunk = response.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return DownloadResult(
                url=url, final_url=final_url, status=status, headers=headers,
                error="too_large",
                error_message=f"body exceeds limit of {max_bytes} bytes",
            )
        chunks.append(chunk)
    return DownloadResult(
        url=url, final_url=final_url, status=status, headers=headers, body=b"".join(chunks)
    )


def _header_dict(message) -> dict[str, str]:
    if message is None:
        return {}
    return {name.lower(): value for name, value in message.items()}


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def follow_redirects(
    url: str,
    headers: dict[str, str] | None,
    downloader: Downloader,
    *,
    max_bytes: int = MAX_FEED_BYTES,
    before_request: Callable[[str], None] | None = None,
    robots: "RobotsPolicy | None" = None,
    max_redirects: int = MAX_REDIRECTS,
) -> DownloadResult:
    """Request ``url``, following up to ``max_redirects`` redirects explicitly.

    The caller is responsible for the robots check of ``url`` itself. If
    ``robots`` is given, every redirect target is checked before it is
    requested (decisions are cached per origin by the policy, so a same-origin
    redirect costs no extra robots.txt request). A blocked target is never
    requested. The returned result's ``url`` is the original URL and
    ``final_url`` the last URL requested (or the blocked target).
    """
    current = url
    visited = {url}
    for hop in range(max_redirects + 1):
        if before_request:
            before_request(current)
        result = downloader(current, headers or {}, max_bytes=max_bytes)
        if result.error or result.status not in REDIRECT_STATUSES:
            return replace(result, url=url, final_url=result.final_url or current)

        location = result.headers.get("location")
        target = urljoin(current, location.strip()) if location and location.strip() else None
        if not target or urlsplit(target).scheme.lower() not in ("http", "https"):
            return _redirect_failure(url, current, result.status, "http_error",
                                     f"HTTP {result.status} redirect without a usable Location"
                                     f" ({location!r})")
        target = target.split("#", 1)[0]
        if target in visited:
            return _redirect_failure(url, target, result.status, "http_error",
                                     f"redirect loop at {target}")
        if hop == max_redirects:
            return _redirect_failure(url, target, result.status, "http_error",
                                     f"too many redirects (limit {max_redirects})")
        if robots is not None:
            decision = robots.check(target)
            if not decision.allowed:
                return _redirect_failure(url, target, result.status, "robots_disallowed",
                                         f"redirected to {target}; {decision.reason}")
        visited.add(target)
        current = target
    raise AssertionError("unreachable")  # pragma: no cover


def _redirect_failure(url, final_url, status, error, message) -> DownloadResult:
    return DownloadResult(url=url, final_url=final_url, status=status,
                          error=error, error_message=message)


# --- robots.txt ----------------------------------------------------------------


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    reason: str


class RobotsPolicy:
    """robots.txt checks with RFC 9309 handling of unavailable files.

    - 2xx: rules are parsed and applied to our User-Agent.
    - 4xx: robots.txt is "unavailable"; fetching is allowed.
    - 5xx, network failure, oversized file, or anything else: "unreachable";
      fetching is not allowed for this run.

    Redirects of robots.txt itself are followed (up to MAX_REDIRECTS), as RFC
    9309 requires. Decisions are cached per origin (scheme + host + port) for
    the lifetime of the object, which the collector keeps to a single run.
    """

    def __init__(self, downloader: Downloader, before_request: Callable[[str], None] | None = None):
        self._downloader = downloader
        self._before_request = before_request
        self._cache: dict[str, tuple[str, urllib.robotparser.RobotFileParser | None, bool]] = {}

    def check(self, url: str) -> RobotsDecision:
        origin = origin_of(url)
        if origin not in self._cache:
            self._cache[origin] = self._load(origin)
        reason, parser, allow_all = self._cache[origin]
        if parser is None:
            return RobotsDecision(allow_all, reason)
        allowed = parser.can_fetch(USER_AGENT, url)
        return RobotsDecision(allowed, reason if allowed else f"disallowed by {origin}/robots.txt")

    def _load(self, origin: str):
        robots_url = f"{origin}/robots.txt"
        result = follow_redirects(
            robots_url, {}, self._downloader,
            max_bytes=MAX_ROBOTS_BYTES, before_request=self._before_request,
        )
        if result.error:
            return (f"robots.txt unreachable ({result.error_message or result.error}); "
                    "treated as disallow", None, False)
        status = result.status or 0
        if 200 <= status < 300:
            parser = urllib.robotparser.RobotFileParser(robots_url)
            parser.parse((result.body or b"").decode("utf-8", errors="replace").splitlines())
            return (f"allowed by {robots_url}", parser, True)
        if 400 <= status < 500:
            return (f"robots.txt unavailable (HTTP {status}); allowed", None, True)
        return (f"robots.txt unreachable (HTTP {status}); treated as disallow", None, False)
