"""robots.txt, read before we read anyone else's website.

Scanning is the one thing this system does to servers that are not ours. It is
user-initiated and small — one site, at most `MAX_PAGES` requests, one at a time
— but "small" is our word for it, not the site owner's, and the file exists so
they can answer without being asked.

We serve our own robots.txt for exactly this reason. `api.main.robots` says yes
in the one place a fetcher looks, because Anthropic's crawler read a 404 as
ambiguity and stopped. Honouring the same file when we are the one fetching is
the other half of that, and it was the open half.

## Matching

`RobotFileParser` takes the first `/`-delimited segment of the User-Agent, so
the token a site writes to address us is `LenniA2B` and the version does not
enter into it. Rules addressed to `*` apply as normal.

## When robots.txt cannot be read

Treated as permission. A 404 means permission under RFC 9309, and a timeout or
a 5xx does not, but the spec's answer there — assume complete disallow — is
written for a continuous crawler with somewhere else to be. Our scan is a person
pasting their own bank's URL and waiting, and refusing it because the site's
robots.txt was briefly slow reports a failure they cannot act on and did not
cause. The exposure is bounded by everything else already true of a scan: same
host, twelve pages, one at a time, an honest User-Agent.

An explicit `Disallow` is different, and is obeyed.
"""
from __future__ import annotations

from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

USER_AGENT = "LenniA2B/0.1 (+https://lenni.io/agents)"
TIMEOUT = 8

# One parsed file per origin, for the length of the process. A scan reads up to
# twelve pages from one host and re-fetching robots.txt for each of them would
# make honouring the file cost more requests than ignoring it.
_cache: dict[str, Optional[RobotFileParser]] = {}


def origin(url: str) -> str:
    """Scheme and host, which is the scope a robots.txt covers."""
    parts = urlparse(url if "://" in url else "https://" + url)
    return urlunparse((parts.scheme or "https", parts.netloc, "", "", "", ""))


def _fetch(robots_url: str) -> Optional[str]:
    import requests

    try:
        resp = requests.get(robots_url, timeout=TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    # A site that serves its 404 page as HTML with a 200 would otherwise be
    # parsed as a rule set, and a stray "Disallow:" in prose would stick.
    if "text/plain" not in resp.headers.get("content-type", "text/plain").lower():
        return None
    return resp.text


def rules(url: str, *, fetch_text: Optional[Callable[[str], Optional[str]]] = None
          ) -> Optional[RobotFileParser]:
    """The parsed robots.txt governing `url`, or None if there is nothing to obey.

    `fetch_text` exists so tests can supply a file without a network.
    """
    root = origin(url)
    if root not in _cache:
        text = (fetch_text or _fetch)(root + "/robots.txt")
        if text is None:
            _cache[root] = None
        else:
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            _cache[root] = parser
    return _cache[root]


def allowed(url: str, *, fetch_text: Optional[Callable[[str], Optional[str]]] = None
            ) -> bool:
    """Whether the site's own robots.txt lets us read this URL."""
    parser = rules(url, fetch_text=fetch_text)
    if parser is None:
        return True
    return parser.can_fetch(USER_AGENT, url)


def reset() -> None:
    """Forget every cached file. For tests, and for a long-lived process that
    should not still be acting on a rule set from last week."""
    _cache.clear()
