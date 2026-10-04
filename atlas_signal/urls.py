"""URL normalization used to recognise articles that have already been seen.

``normalize_url`` is a pure function: it never touches the network and returns
the same output for the same input. It deliberately avoids aggressive
rewriting; the only changes are the ones listed in its docstring.
"""

from urllib.parse import unquote, urlsplit

# Tracking query parameters, matched case-insensitively against the decoded
# parameter name. This is the single place to review or extend the list.
TRACKING_PARAM_PREFIXES = ("utm_", "at_")
TRACKING_PARAM_NAMES = frozenset({"cmp", "fbclid", "gclid", "ocid"})

_DEFAULT_PORTS = frozenset({80, 443})


def is_tracking_param(name: str) -> bool:
    """Return True if a raw query parameter name is a known tracking parameter."""
    key = unquote(name).lower()
    return key in TRACKING_PARAM_NAMES or key.startswith(TRACKING_PARAM_PREFIXES)


def normalize_url(url: str) -> str:
    """Return the normalized form of an absolute HTTP(S) URL.

    Changes made:
    - surrounding whitespace is trimmed;
    - the scheme becomes ``https`` (``http`` and ``https`` are treated as equal);
    - the hostname is lowercased (``www.`` is kept as given);
    - ports 80 and 443 are removed;
    - the fragment is removed;
    - an empty path becomes ``/``; any other path is kept exactly as given;
    - tracking query parameters (see ``is_tracking_param``) are removed;
    - remaining query parameters are kept exactly as given (including
      percent-encoding and duplicates) and stably sorted by raw name, so
      repeated parameters keep their original relative order.

    Raises:
        TypeError: if ``url`` is not a string.
        ValueError: if ``url`` is not an absolute http/https URL with a host,
            contains whitespace or control characters after trimming, contains
            user credentials, or has an invalid port.
    """
    if not isinstance(url, str):
        raise TypeError(f"url must be a string, not {type(url).__name__}")

    text = url.strip()
    if not text:
        raise ValueError("URL is empty")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError(f"URL contains whitespace or control characters: {url!r}")

    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"malformed URL: {url!r} ({exc})") from exc

    if parts.scheme not in ("http", "https"):
        raise ValueError(f"not an absolute http/https URL: {url!r}")
    if not parts.hostname:
        raise ValueError(f"URL has no host: {url!r}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"URL contains user credentials: {url!r}")

    host = parts.hostname.lower()
    if ":" in host:  # IPv6 literal; urlsplit strips the brackets
        host = f"[{host}]"
    if port is not None and port not in _DEFAULT_PORTS:
        host = f"{host}:{port}"

    path = parts.path or "/"

    kept = [
        param
        for param in parts.query.split("&")
        if param and not is_tracking_param(param.split("=", 1)[0])
    ]
    kept.sort(key=lambda param: param.split("=", 1)[0])
    query = "&".join(kept)

    return f"https://{host}{path}" + (f"?{query}" if query else "")
