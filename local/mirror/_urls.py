"""Shared URL/host matching helpers for the mirror layer.

`hostname_matches(domain, url)` is the correct replacement for the substring
check `domain in url` that we used to scatter across browser/router/workflows.
The substring approach silently confused providers whose domains happened to
be substrings of each other (notably `dbet.com` ⊂ `cloudbet.com`), so any
operation routed for `dbet` could land on the cloudbet tab.
"""

from __future__ import annotations

from urllib.parse import urlparse


def hostname_matches(domain: str, url: str) -> bool:
    """True iff `url`'s hostname equals `domain` or is a subdomain of it.

    Case-insensitive. Strips a single leading `www.` from both sides so apex
    and `www` variants compare equal. Returns False on parse failure or empty
    inputs — never raises.
    """
    if not domain or not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host.startswith("www."):
        host = host[4:]
    d = domain.strip().lower().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if not d:
        return False
    return host == d or host.endswith("." + d)
