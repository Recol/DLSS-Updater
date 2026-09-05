"""Tests for the Playnite library reader."""

import os
import struct
from pathlib import Path

import bson
import pytest

from dlss_updater import playnite
from dlss_updater.utils import find_game_root

PAGE_SIZE = 4096
HEADER_OFFSET = 25
MAGIC = b"** This is a LiteDB file **"
BLOCK = struct.Struct("<HIH")
NO_PAGE = 0xFFFFFFFF


@pytest.fixture
def isolated_candidates(tmp_path, monkeypatch):
    """Point the standard library candidates away from any real install."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(Path, "home", lambda: fake_home)


def _header_page():
    page = bytearray(PAGE_SIZE)
    page[4] = 1  # header page type
    page[HEADER_OFFSET:HEADER_OFFSET + len(MAGIC)] = MAGIC
    return bytes(page)


def _data_page(payload):
    page = bytearray(PAGE_SIZE)
    page[4] = 4
    struct.pack_into("<H", page, 13, 1)  # one item
    BLOCK.pack_into(page, HEADER_OFFSET, 0, NO_PAGE, len(payload))
    end = HEADER_OFFSET + BLOCK.size + len(payload)
    page[HEADER_OFFSET + BLOCK.size:end] = payload
    return bytes(page)


def _write_library(root, docs):
    library = root / "library"
    library.mkdir(parents=True, exist_ok=True)
    pages = [_header_page()]
    for doc in docs:
        pages.append(_data_page(bson.encode(doc)))
    (library / "games.db").write_bytes(b"".join(pages))
    return library


def _game_doc(name="Quake", install_dir=r"C:\Games\Quake", installed=True):
    doc = {"IsInstalled": installed}
    if name is not None:
        doc["Name"] = name
    if install_dir is not None:
        doc["InstallDirectory"] = install_dir
    return doc


class TestLoadPlayniteGames:
    def test_reads_installed_games_with_existing_dirs(self, tmp_path, isolated_candidates):
        (tmp_path / "Quake").mkdir()
        (tmp_path / "Doom").mkdir()
        _write_library(tmp_path, [
            _game_doc("Quake", str(tmp_path / "Quake")),
            _game_doc("Uninstalled", str(tmp_path / "Doom"), installed=False),
            _game_doc("Missing Dir", r"C:\Not\A\Real\Dir"),
            _game_doc("Variable", r"{InstallDir}\bin"),
        ])

        assert playnite.load_playnite_games([str(tmp_path)]) == [
            ("Quake", str(tmp_path / "Quake"))
        ]

    def test_relative_install_dirs_anchor_at_library_root(self, tmp_path, isolated_candidates):
        target = tmp_path / "root" / "library" / "RelGame"
        target.mkdir(parents=True)
        library = _write_library(tmp_path / "root", [_game_doc("Rel", "RelGame")])

        assert playnite.load_playnite_games([str(library)]) == [("Rel", str(target))]

    def test_invalid_database_is_skipped(self, tmp_path, isolated_candidates):
        configured = tmp_path / "configured"
        configured.mkdir()
        (configured / "games.db").write_bytes(b"garbage" * 64)

        assert playnite.load_playnite_games([str(configured)]) == []

    @pytest.mark.skipif(os.name != "nt", reason="msvcrt byte-range locks are Windows-only")
    def test_locked_database_returns_empty(self, tmp_path, isolated_candidates):
        import msvcrt

        library = _write_library(tmp_path, [_game_doc()])
        db_file = library / "games.db"
        size = db_file.stat().st_size
        with open(db_file, "r+b") as handle:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
            try:
                assert playnite.load_playnite_games([str(library)]) == []
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)

    def test_appdata_candidate_used_without_configuration(self, tmp_path, isolated_candidates):
        game_dir = tmp_path / "Game"
        game_dir.mkdir()
        _write_library(tmp_path / "appdata" / "Playnite",
                       [_game_doc("FromAppData", str(game_dir))])

        assert playnite.load_playnite_games(None) == [
            ("FromAppData", str(game_dir))
        ]


class TestRootRegistry:
    def test_root_for_maps_nested_dlls_to_deepest_registered_root(self, tmp_path):
        outer = tmp_path / "Outer Game"
        inner = outer / "Inner Game"
        inner.mkdir(parents=True)
        playnite.register_roots([
            ("Outer", str(outer)),
            ("Inner", str(inner)),
        ])
        try:
            dll = inner / "bin" / "nvngx_dlss.dll"
            assert playnite.root_for(dll) == inner
            assert playnite.root_for(outer / "other.dll") == outer
            assert playnite.root_for(tmp_path / "unrelated.dll") is None
            assert playnite.title_for(inner) == "Inner"
        finally:
            playnite.register_roots([])

    def test_find_game_root_prefers_registered_playnite_roots(self, tmp_path):
        root = tmp_path / "Real Root"
        dll_dir = root / "Retail"
        dll_dir.mkdir(parents=True)
        playnite.register_roots([("Real Title", str(root))])
        try:
            dll = dll_dir / "nvngx_dlss.dll"
            dll.write_bytes(b"MZ")

            assert find_game_root(dll, "Playnite") == root
        finally:
            playnite.register_roots([])
