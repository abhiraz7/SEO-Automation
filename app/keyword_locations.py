"""
Location mapping for the keyword providers. Semrush addresses markets with a
country database code ("in", "us", "uk", ...); DataForSEO with a numeric
location_code from their published locations list. Both are keyed here off one
ISO 3166-1 alpha-2 country code so callers only ever speak ISO.

Unsupported codes are rejected by the adapters (explicit error), never silently
mapped to the US -- a US-index lookup for an Indian keyword returns "no data"
from a perfectly healthy API, which is exactly the silent failure this exists
to prevent (spec Bug 2).

Country-level granularity only for now. DataForSEO supports city-level codes;
add them here (and a second selector in the UI) if/when a client needs it.
"""

# Deliberate default, not a hidden hardcode: every lookup threads an explicit
# location and the UI selector always shows which market is active. Changed
# IN -> US per user instruction 2026-07-19 (was India-first before that);
# flip this one constant to change the default market app-wide.
DEFAULT_LOCATION = "US"

# ISO code -> (display name, semrush database, dataforseo location_code)
_LOCATIONS: dict[str, tuple[str, str, int]] = {
    "IN": ("India", "in", 2356),
    "US": ("United States", "us", 2840),
    "GB": ("United Kingdom", "uk", 2826),
    "AU": ("Australia", "au", 2036),
    "CA": ("Canada", "ca", 2124),
    "AE": ("United Arab Emirates", "ae", 2784),
    "SG": ("Singapore", "sg", 2702),
    "DE": ("Germany", "de", 2276),
    "FR": ("France", "fr", 2250),
}


def supported_locations() -> dict[str, str]:
    """ISO code -> display name, for UI selectors."""
    return {code: name for code, (name, _, _) in _LOCATIONS.items()}


def is_supported(location: str) -> bool:
    return location.upper() in _LOCATIONS


def semrush_database(location: str) -> str | None:
    entry = _LOCATIONS.get(location.upper())
    return entry[1] if entry else None


def dataforseo_location_code(location: str) -> int | None:
    entry = _LOCATIONS.get(location.upper())
    return entry[2] if entry else None
