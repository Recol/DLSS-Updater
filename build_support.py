"""Build-time helpers shared by the Windows PyInstaller spec files.

Two jobs: bundle the Flet desktop client, and stamp the Windows version
resource onto the executable.

--- The Flet desktop client ---

Make sure the client ships *inside* the build instead of being downloaded on
first launch.

`flet_desktop` publishes no client binary in its wheel - the package is two
Python files. At startup `flet_desktop.ensure_client_cached()` resolves the
client in this order:

  1. the extracted cache at ``~/.flet/client/flet-desktop-<flavor>-<version>``
  2. a bundled archive at ``<flet_desktop>/app/<artifact>``  <- the PyInstaller path
  3. download ``<artifact>`` from the flet GitHub release

On a developer machine step 1 always hits, because running from source
populates that cache. On a genuinely fresh machine it does not, so shipped
builds fell through to step 3 and downloaded ~40MB over HTTPS before the
window ever appeared. Issue #265: a clean Windows 11 install whose root store
could not verify GitHub's certificate crashed on launch with
SSLCertVerificationError, from urllib inside flet_desktop.

Placing the archive at step 2 removes the download - and with it the
dependency on the user's trust store, proxy and connectivity at launch.

Since Flet 1.0 a bundled archive is also content-fingerprinted, and the
fingerprint picks the cache directory, so step 1 cannot even be checked
without it. flet_desktop reads it from an ``<archive>.sha256`` sidecar when
one is present, and otherwise SHA-256s the whole ~40MB archive and tries to
cache the result next to it. In the MSI install directory (not writable by
the user) and in a onefile build's fresh ``_MEIPASS`` that write can never
stick, so every launch would pay the full hash. The sidecar is therefore
computed here, at build time, and shipped beside the archive.

--- The Windows version resource ---

`windows_version_info()` builds the VS_VERSIONINFO that both Windows specs
stamp onto DLSS_Updater.exe. See its docstring for why an executable with no
publisher metadata is a liability.

Run directly to fetch the archive into the build cache:

    uv run python build_support.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# Deliberately NOT under build/ - build_msi.ps1 wipes that directory on every
# run, which would re-download 40MB each time.
CACHE_DIR = REPO_ROOT / ".build_cache" / "flet"

# Anything smaller than this is a truncated download or an error page, not a
# client. The 0.86.4 Windows archive is ~40MB.
_MIN_PLAUSIBLE_BYTES = 5 * 1024 * 1024


def _flet_desktop():
    try:
        import flet_desktop
    except ImportError as exc:  # pragma: no cover - build environment only
        raise SystemExit(
            "flet_desktop is not installed - run `uv sync --frozen --extra build` first."
        ) from exc
    return flet_desktop


def artifact_name() -> str:
    """Archive filename for this platform and desktop flavor (e.g. flet-windows.zip).

    Taken from flet_desktop itself rather than hardcoded, so a flavor or
    naming change cannot silently desync the bundled file from the one
    ensure_client_cached() looks for.
    """
    return _flet_desktop().get_artifact_filename()


def client_version() -> str:
    return _flet_desktop().version.version


def client_url() -> str:
    return (
        f"https://github.com/flet-dev/flet/releases/download/"
        f"v{client_version()}/{artifact_name()}"
    )


def client_archive_path() -> Path:
    # Version-scoped: a flet bump must not silently reuse the old client.
    return CACHE_DIR / client_version() / artifact_name()


def _is_valid_archive(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < _MIN_PLAUSIBLE_BYTES:
        return False
    # A truncated download or an HTML error page saved under a .zip name would
    # otherwise be bundled and fail at the user's first launch, which is
    # exactly the failure mode this whole module exists to prevent.
    return zipfile.is_zipfile(path)


def fingerprint_sidecar_path(archive: Path) -> Path:
    """Where flet_desktop looks for the archive's pre-computed fingerprint."""
    return archive.with_name(archive.name + ".sha256")


def ensure_fingerprint_sidecar(archive: Path) -> Path:
    """Write ``<archive>.sha256`` in the ``"<sha256 hex> <size>"`` form flet reads.

    Always re-hashes rather than trusting an existing sidecar's size check:
    this runs at build time, where the hash costs well under a second, and a
    stale same-size sidecar would ship a fingerprint that points the app at
    another client's cache directory. Only rewrites the file when it changed.
    """
    digest = hashlib.sha256()
    with archive.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    expected = f"{digest.hexdigest()} {archive.stat().st_size}"

    sidecar = fingerprint_sidecar_path(archive)
    try:
        if sidecar.read_text(encoding="ascii") == expected:
            return sidecar
    except (OSError, UnicodeDecodeError):
        pass

    # Temp file + replace, so an interrupted build never leaves a truncated
    # sidecar that flet would reject and silently fall back from.
    tmp = sidecar.with_name(sidecar.name + ".part")
    tmp.write_text(expected, encoding="ascii", newline="")
    tmp.replace(sidecar)
    print(f"Flet client fingerprint written: {sidecar}")
    return sidecar


def ensure_client_archive() -> Path:
    """Download the client archive into the build cache if it isn't there.

    Returns the path to a verified archive, with its fingerprint sidecar
    written next to it. Safe to call repeatedly.
    """
    target = client_archive_path()
    if _is_valid_archive(target):
        print(f"Flet client archive already cached: {target}")
        ensure_fingerprint_sidecar(target)
        return target

    if target.exists():
        print(f"Cached archive at {target} is invalid - refetching")
        target.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)
    url = client_url()
    print(f"Downloading Flet client {client_version()} from {url}")

    # Download to a temp file and move into place, so an interrupted build
    # never leaves a half-written archive that looks cached.
    with tempfile.NamedTemporaryFile(delete=False, dir=target.parent, suffix=".part") as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(url) as response, tmp_path.open("wb") as out:
            shutil.copyfileobj(response, out)
        if not _is_valid_archive(tmp_path):
            raise SystemExit(
                f"Downloaded Flet client from {url} is not a valid archive "
                f"({tmp_path.stat().st_size} bytes). Refusing to bundle it."
            )
        tmp_path.replace(target)
    finally:
        tmp_path.unlink(missing_ok=True)

    print(f"Flet client cached: {target} ({target.stat().st_size / 1024 / 1024:.1f} MB)")
    ensure_fingerprint_sidecar(target)
    return target


def flet_client_datas() -> list[tuple[str, str]]:
    """PyInstaller ``datas`` entries placing the client where flet looks for it.

    Ships the archive together with its ``.sha256`` fingerprint sidecar, so
    the frozen app reads the fingerprint instead of hashing ~40MB on every
    launch (see the module docstring).

    Raises rather than returning empty: shipping without the archive is
    invisible on any machine with a populated ~/.flet cache (i.e. every
    developer machine) and only breaks for end users on a clean install. That
    is precisely how issue #265 escaped into a release, so the build fails
    loudly instead.
    """
    archive = client_archive_path()
    if not _is_valid_archive(archive):
        raise SystemExit(
            f"\nMissing Flet desktop client archive: {archive}\n"
            f"Without it the app downloads the client at first launch and dies on\n"
            f"any machine that cannot verify GitHub's certificate (issue #265).\n\n"
            f"Fetch it with:  uv run python build_support.py\n"
        )
    sidecar = ensure_fingerprint_sidecar(archive)
    return [(str(archive), "flet_desktop/app"), (str(sidecar), "flet_desktop/app")]


# =============================================================================
# Windows version resource
# =============================================================================

# Fixed publisher metadata. Kept here rather than read from pyproject.toml so
# the spec has no TOML dependency at Analysis time; only the version number
# actually changes between releases, and that IS read from the source of truth.
_COMPANY_NAME = "Recol (Deco)"
_PRODUCT_NAME = "DLSS Updater"
_FILE_DESCRIPTION = "DLSS, XeSS, DirectStorage, FSR and Streamline DLL updater for games"
_COPYRIGHT = "Copyright (C) 2024-2026 Recol (Deco). Licensed under AGPL-3.0-only."
_ORIGINAL_FILENAME = "DLSS_Updater.exe"


def app_version() -> str:
    """``__version__`` from dlss_updater/version.py, read without importing it.

    Parsed rather than imported so the spec never pays for (or fails on) the
    package's import side effects during Analysis. version.py is the release
    checklist's first entry, which makes it the right single source.
    """
    source = (REPO_ROOT / "dlss_updater" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', source)
    if not match:
        raise SystemExit(
            "Could not read __version__ from dlss_updater/version.py - "
            "the Windows version resource cannot be built."
        )
    return match.group(1)


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    """``"5.0.2"`` -> ``(5, 0, 2, 0)``.

    VS_FIXEDFILEINFO is four 16-bit words, always - a short version is padded
    and a suffixed component ("1.2.3rc1") keeps only its leading digits, since
    anything non-numeric there would abort the build over cosmetic metadata.
    """
    parts: list[int] = []
    for piece in version.split(".")[:4]:
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group()) if digits else 0)
    parts.extend([0] * (4 - len(parts)))
    return tuple(parts)  # type: ignore[return-value]


def windows_version_info():
    """The VS_VERSIONINFO resource for DLSS_Updater.exe.

    Why this exists: the shipped executable is unsigned, and before this it
    also carried no version resource at all - no CompanyName, no ProductName,
    no FileVersion. That leaves Windows Defender's ML classifier with nothing
    to anchor on beyond a brand-new PyInstaller bootloader with no prevalence,
    which is how 5.0.2 drew a Trojan:Script/Wacatac.C!ml verdict hours after
    release (issue #306). Publisher metadata is not a substitute for a
    signature, but it is the part that costs nothing.

    It also fixes the visible symptoms of the same gap: the Properties dialog
    shows a version, Task Manager shows a description instead of the bare file
    name, and the AUMID work in build_msi.ps1 gets a properly labelled window.

    Returned as a ``VSVersionInfo`` instance rather than a generated text file
    because ``EXE(version=...)`` accepts either (PyInstaller 6.x), and an
    object cannot desync from version.py the way a checked-in file would.
    """
    # Imported lazily: this module is also run standalone (``python
    # build_support.py``) to prime the client cache, where PyInstaller's
    # Windows-only helpers need not be importable.
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version = app_version()
    numeric = _version_tuple(version)

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numeric,
            prodvers=numeric,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,     # VOS_NT_WINDOWS32
            fileType=0x1,   # VFT_APP
            subtype=0x0,
        ),
        kids=[
            # 0409 = en-US, 04B0 = 1200 = Unicode. The StringTable key and the
            # Translation pair below must agree, or Explorer reads neither.
            StringFileInfo([
                StringTable("040904B0", [
                    StringStruct("CompanyName", _COMPANY_NAME),
                    StringStruct("FileDescription", _FILE_DESCRIPTION),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", _PRODUCT_NAME),
                    StringStruct("LegalCopyright", _COPYRIGHT),
                    StringStruct("OriginalFilename", _ORIGINAL_FILENAME),
                    StringStruct("ProductName", _PRODUCT_NAME),
                    StringStruct("ProductVersion", version),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


if __name__ == "__main__":
    ensure_client_archive()
    sys.exit(0)
