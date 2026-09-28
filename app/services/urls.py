"""Source link normalization: one spelling per link, so the same page is one work_sources row.

The migration 982606565e2e keeps a frozen copy of normalize_url; change both together.
"""

from urllib.parse import urlsplit, urlunsplit

# (Postgres regex) a link with a path: used by the unique indexes on work_sources.url
WORK_LINK_SQL = "url ~ '^[^:]+://[^/]+/.'"


def normalize_url(url: str) -> str:
    """Lowercase scheme and host, drop default ports, the fragment and a trailing slash; keep the query.

    https://WWW.Example.com:443/manga/abc/#top → https://www.example.com/manga/abc
    """
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    if parts.username:
        host = f"{parts.username}@{host}"
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def identifies_work(url: str) -> bool:
    """A page link (https://site/manga/abc) points at one work; a bare site (https://site) doesn't.

    The frontend fills in the site's home page when no link is given, so bare sites must never be
    used to match or deduplicate works.
    """
    try:
        return bool(urlsplit(normalize_url(url)).path.strip("/"))
    except ValueError:
        return False
