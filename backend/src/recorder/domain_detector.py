"""
Domain Detector — maps browser URLs to provider IDs.

Uses PROVIDER_DOMAINS from constants.py to detect which bookmaker
the user is currently browsing.
"""

from urllib.parse import urlparse

from ..constants import PROVIDER_DOMAINS


def detect_provider(url: str) -> str | None:
    """
    Extract provider_id from a URL by matching its hostname against PROVIDER_DOMAINS.

    Strips 'www.' prefix and checks progressively shorter domain suffixes
    to handle subdomains (e.g., 'arcadia.pinnacle.com' → 'pinnacle.com' → 'pinnacle').

    Returns None for unrecognized domains.
    """
    if not url:
        return None

    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return None
    except Exception:
        return None

    # Strip www. prefix
    if hostname.startswith("www."):
        hostname = hostname[4:]

    # Direct match first
    if hostname in PROVIDER_DOMAINS:
        return PROVIDER_DOMAINS[hostname]

    # Try progressively shorter suffixes (for subdomains)
    parts = hostname.split(".")
    for i in range(1, len(parts) - 1):
        suffix = ".".join(parts[i:])
        if suffix in PROVIDER_DOMAINS:
            return PROVIDER_DOMAINS[suffix]

    return None
