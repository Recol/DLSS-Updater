"""
Shared game-name normalization for Steam name matching and FTS search.

Single source of truth so that the FTS index build
(``steam_integration.download_steam_app_list``), the one-time FTS migration
backfill (``database._migrate_steam_fts_schema``) and the query-side
normalization (``database._search_steam_app``) all produce IDENTICAL tokens.
If these ever diverge, multi-word / punctuated searches silently stop matching
(issue #246), so keep the transformation here and here only.

Pure stdlib, imports nothing from the app, so it can be imported anywhere
(``database.py`` imports it at module load) without circular-import risk.
"""

import re

# Pre-compiled once (module import) — normalize_search_name runs ~200K times
# during a full Steam app-list migration backfill, so avoid per-call re.compile.
_TRADEMARK_RE = re.compile(r"[™®©]")
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]")
_MULTISPACE_RE = re.compile(r"\s+")


def normalize_search_name(name: str) -> str:
    """Normalize a game name for search matching, KEEPING word spaces.

    Lowercase, strip trademark symbols, drop a leading ``"the "``, remove every
    character that is not a lowercase letter / digit / whitespace, then collapse
    runs of whitespace to single spaces.

    Example::

        "S.T.A.L.K.E.R. 2: Heart of Chornobyl™" -> "stalker 2 heart of chornobyl"

    The retained spaces are what let FTS5 tokenize the value into real word
    tokens (``stalker``, ``2``, ``heart`` ...) so multi-word prefix queries
    match. The space-less variant (:func:`normalize_search_name_spaceless`) is
    only useful for exact-match lookup.
    """
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = _TRADEMARK_RE.sub("", normalized)
    if normalized.startswith("the "):
        normalized = normalized[4:]
    normalized = _NON_ALNUM_SPACE_RE.sub("", normalized)
    normalized = _MULTISPACE_RE.sub(" ", normalized).strip()
    return normalized


def normalize_search_name_spaceless(name: str) -> str:
    """Space-less normalized form used for exact-match lookups.

    e.g. "S.T.A.L.K.E.R. 2: Heart of Chornobyl" -> "stalker2heartofchornobyl".
    Used by ``get_steam_app_by_name`` and the LIKE search fallback.
    """
    return normalize_search_name(name).replace(" ", "")
