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

# Display-only prettification (see prettify_display_name). Pre-compiled at module
# level — applied to every visible card title, and this module is imported anywhere.
_HAS_WHITESPACE_RE = re.compile(r"\s")
# ALLCAPS run -> Titlecase word boundary: "DLSSUpdater" -> "DLSS Updater". Must run
# BEFORE the lower->Upper split so the trailing capital of the run is handed to the
# new word ("DLSS" | "Updater"), not consumed as part of the run.
_ACRONYM_WORD_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
# lowercase -> Uppercase boundary: "MarvelRivals" -> "Marvel Rivals".
_LOWER_UPPER_RE = re.compile(r"([a-z])([A-Z])")
# letter -> digit boundary: "ForzaHorizon6" -> "Forza Horizon 6".
_LETTER_DIGIT_RE = re.compile(r"([A-Za-z])([0-9])")


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


def prettify_display_name(name: str) -> str:
    """Insert word spaces into a run-together folder-derived title, for DISPLAY only.

    Scanned game titles are often folder names with the spaces stripped
    ("MarvelRivals", "ForzaHorizon6"), which sit awkwardly next to already-spaced
    titles ("No Man's Sky"). This inserts spaces at the obvious word boundaries so
    they read consistently on the card.

    Rules (applied only to names that contain NO whitespace already):
      - ALLCAPS run -> Titlecase word:  "DLSSUpdater"   -> "DLSS Updater"
      - lowercase -> Uppercase:          "MarvelRivals"  -> "Marvel Rivals"
      - letter -> digit:                 "ForzaHorizon6" -> "Forza Horizon 6"

    Any name that ALREADY contains whitespace is returned UNCHANGED — the scanner
    kept its real spacing/punctuation and we must not mangle it. This also preserves
    intentional all-caps runs in such names.

    This is a DISPLAY helper only. Never feed its output back into DB values, search,
    matching, or path handling — those all rely on the raw ``name`` / ``display_name``.

    Examples::

        prettify_display_name("BlackMythWukong")   -> "Black Myth Wukong"
        prettify_display_name("ForzaHorizon6")      -> "Forza Horizon 6"
        prettify_display_name("MarvelRivals")       -> "Marvel Rivals"
        prettify_display_name("DLSSUpdater")        -> "DLSS Updater"
        prettify_display_name("ARC Raiders")        -> "ARC Raiders"     # unchanged
        prettify_display_name("No Man's Sky")       -> "No Man's Sky"    # unchanged
        prettify_display_name("Baldurs Gate 3")     -> "Baldurs Gate 3"  # unchanged
    """
    if not name or _HAS_WHITESPACE_RE.search(name):
        return name
    result = _ACRONYM_WORD_RE.sub(r"\1 \2", name)
    result = _LOWER_UPPER_RE.sub(r"\1 \2", result)
    result = _LETTER_DIGIT_RE.sub(r"\1 \2", result)
    return result


def normalize_search_name_spaceless(name: str) -> str:
    """Space-less normalized form used for exact-match lookups.

    e.g. "S.T.A.L.K.E.R. 2: Heart of Chornobyl" -> "stalker2heartofchornobyl".
    Used by ``get_steam_app_by_name`` and the LIKE search fallback.
    """
    return normalize_search_name(name).replace(" ", "")
