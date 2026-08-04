"""Build-time helpers shared by the Windows PyInstaller spec files.

Exists for one job: make sure the Flet desktop client ships *inside* the
build instead of being downloaded on first launch.

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

Run directly to fetch the archive into the build cache:

    uv run python build_support.py
"""

from __future__ import annotations

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


def ensure_client_archive() -> Path:
    """Download the client archive into the build cache if it isn't there.

    Returns the path to a verified archive. Safe to call repeatedly.
    """
    target = client_archive_path()
    if _is_valid_archive(target):
        print(f"Flet client archive already cached: {target}")
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
    return target


def flet_client_datas() -> list[tuple[str, str]]:
    """PyInstaller ``datas`` entry placing the client where flet looks for it.

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
    return [(str(archive), "flet_desktop/app")]


if __name__ == "__main__":
    ensure_client_archive()
    sys.exit(0)
