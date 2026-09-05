"""Read installed games from a local Playnite installation.

Playnite stores its library as one LiteDB v4 file per collection inside a
"library" directory (games.db holds every game document). The container is a
plain sequence of 4096-byte pages; game documents are BSON records framed by
8-byte block headers inside pages of type 4. Document decoding uses the bson
package, matching the format Playnite itself writes.
"""

import logging
import os
import struct
from pathlib import Path

import bson

logger = logging.getLogger(__name__)

_PAGE_SIZE = 4096
_HEADER_OFFSET = 25  # page header size in LiteDB v4
_MAGIC = b"** This is a LiteDB file **"
_NO_PAGE = 0xFFFFFFFF
_PAGE_TYPE_DATA = 4
_PAGE_TYPE_EXTEND = 5
_BLOCK_HEADER = struct.Struct("<HIH")  # slot id, extend page id, inline size
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")

# Install roots registered for the last resolved library: normalized root
# path -> (original root string, game title). Used to map DLLs back to their
# game during grouping and to keep real titles instead of folder names.
_known_roots: dict[str, tuple[str, str]] = {}


def register_roots(pairs):
    """Remember (title, install_dir) pairs returned by load_playnite_games."""
    _known_roots.clear()
    for title, install_dir in pairs:
        try:
            key = os.path.normcase(str(Path(install_dir).resolve()))
        except OSError:
            continue
        _known_roots[key] = (str(install_dir), title)


def root_for(dll_path):
    """Deepest registered Playnite install root that contains dll_path."""
    probe = os.path.normcase(str(dll_path))
    best_key = None
    for key in _known_roots:
        if probe.startswith(key + os.sep) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return None
    return Path(_known_roots[best_key][0])


def title_for(game_dir):
    """Registered game title for an install root, or None."""
    entry = _known_roots.get(os.path.normcase(str(game_dir)))
    return entry[1] if entry else None


def _extend_chain(data, page_id):
    """Follow an extend-page chain and concatenate its stored bytes."""
    parts = []
    seen = set()
    while page_id != _NO_PAGE and page_id not in seen:
        seen.add(page_id)
        start = page_id * _PAGE_SIZE
        if start + _PAGE_SIZE > len(data):
            break
        page = data[start:start + _PAGE_SIZE]
        if page[4] != _PAGE_TYPE_EXTEND:
            break
        page_id = _U32.unpack_from(page, 9)[0]
        stored = _U16.unpack_from(page, 13)[0]
        parts.append(page[_HEADER_OFFSET:_HEADER_OFFSET + stored])
    return b"".join(parts)


def iter_game_documents(data):
    """Yield decoded game documents from games.db bytes.

    Every .db file holds exactly one collection, so scanning each type-4
    data page covers all documents without walking indexes. Documents too
    large for one page live in linked extend-page chains (inline size 0).
    """
    header = data[_HEADER_OFFSET:_HEADER_OFFSET + len(_MAGIC)]
    if len(data) < _PAGE_SIZE or not header.startswith(_MAGIC):
        raise ValueError("not a LiteDB v4 database")

    for start in range(_PAGE_SIZE, len(data), _PAGE_SIZE):
        page = data[start:start + _PAGE_SIZE]
        if len(page) < _HEADER_OFFSET or page[4] != _PAGE_TYPE_DATA:
            continue
        item_count = _U16.unpack_from(page, 13)[0]
        cursor = _HEADER_OFFSET
        for _ in range(item_count):
            if cursor + _BLOCK_HEADER.size > len(page):
                break
            _, extend_page, inline_size = _BLOCK_HEADER.unpack_from(page, cursor)
            cursor += _BLOCK_HEADER.size
            payload = (
                _extend_chain(data, extend_page)
                if extend_page != _NO_PAGE
                else page[cursor:cursor + inline_size]
            )
            cursor += inline_size
            yield bson.decode(payload)


def _game_entry(doc, library_root):
    """Return (title, install_dir) for an installed game document, or None."""
    title = doc.get("Name")
    if not isinstance(title, str) or not title:
        return None
    if doc.get("IsInstalled") is not True:
        return None
    raw = doc.get("InstallDirectory")
    if not isinstance(raw, str) or not raw or "{" in raw:
        return None
    install_dir = Path(os.path.expandvars(raw))
    if not install_dir.is_absolute():
        install_dir = library_root / install_dir
    if not install_dir.is_dir():
        return None
    return title, install_dir


def _library_candidates(configured_paths):
    """Library directories to probe, most specific first."""
    candidates = []
    for configured in configured_paths or []:
        root = Path(configured)
        candidates.append(root / "library" if (root / "library").is_dir() else root)
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Playnite" / "library")
    if os.name == "nt":
        candidates.append(Path.home() / "scoop" / "persist" / "playnite" / "library")
    return candidates


def load_playnite_games(configured_paths=None):
    """Return (title, install_dir) pairs for installed Playnite games.

    Probes the configured library directories first, then the standard
    install locations. Returns [] when no readable database is found, e.g.
    while Playnite is running and keeps games.db exclusively locked.
    """
    for library in _library_candidates(configured_paths):
        db_file = library / "games.db"
        if not db_file.is_file():
            continue
        try:
            data = db_file.read_bytes()
            documents = list(iter_game_documents(data))
        except OSError as e:
            logger.warning(f"Cannot read Playnite database {db_file}: {e}")
            continue
        except Exception as e:
            logger.warning(f"Skipping unreadable Playnite database {db_file}: {e}")
            continue

        games = []
        for doc in documents:
            entry = _game_entry(doc, library)
            if entry:
                games.append((entry[0], str(entry[1])))
        if games:
            return games

    logger.info("No readable Playnite library found")
    return []
