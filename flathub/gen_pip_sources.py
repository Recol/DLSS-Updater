#!/usr/bin/env python3
"""Generate flatpak offline pip sources for the cp314t (free-threaded) interpreter.

Reads a uv-exported requirements.txt (produced by build_flathub.sh via
`uv export --frozen --no-emit-project --no-dev --no-hashes`), filters
non-Linux and packaging-toolchain entries, queries PyPI's JSON API for each
pin, and picks per package:

  1. cp314(t) manylinux x86_64 wheel   (native, free-threaded)
  2. py3-none-any wheel                (pure python)
  3. sdist                             (flagged: must compile in flatpak-builder)

Writes generated/python-deps.json (a flatpak module) and a summary to stdout.
The standard flatpak-pip-generator can't do this: it targets the SDK's own
python, not a custom-built cp314t interpreter.

Usage: gen_pip_sources.py <requirements.txt> <output.json>
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

REQS = Path(sys.argv[1])
OUT = Path(sys.argv[2])

# PyInstaller toolchain: a main [project] dependency (used by the MSI and
# bundle-flatpak builds) but not part of the app at runtime - the Flathub
# build runs straight from site-packages. uv can't exclude a single main
# dep on export, so filter here.
EXCLUDE = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "macholib", "briefcase"}
WIN_MARKER = re.compile(r"==\s*['\"](win32|cygwin|darwin|Windows)['\"]")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


entries = []
for raw in REQS.read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    marker = ""
    if ";" in line:
        line, marker = [p.strip() for p in line.split(";", 1)]
    if "==" not in line:
        continue
    name, ver = line.split("==")
    name, ver = norm(name.strip()), ver.strip()
    if name in EXCLUDE:
        continue
    # Keep anything that can apply on Linux. Only drop entries whose marker
    # names win/mac and never mentions linux (e.g. "sys_platform == 'win32'").
    # OR-markers like "sys_platform == 'linux' or sys_platform == 'win32'"
    # must be kept.
    if marker and "linux" not in marker.lower() and WIN_MARKER.search(marker):
        continue
    entries.append((name, ver))

entries = sorted(set(entries))
sources = []
summary = {"native-cp314t": [], "pure": [], "sdist": [], "MISSING": []}

for name, ver in entries:
    url = f"https://pypi.org/pypi/{name}/{ver}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        summary["MISSING"].append(f"{name}=={ver} ({e})")
        continue
    native = pure = sdist = None
    for f in data.get("urls", []):
        fn = f["filename"]
        if fn.endswith(".whl") and "cp314" in fn and "cp314t" in fn and "manylinux" in fn and "x86_64" in fn:
            native = native or f
        elif fn.endswith("py3-none-any.whl"):
            pure = pure or f
        elif fn.endswith((".tar.gz", ".zip")) and f["packagetype"] == "sdist":
            sdist = sdist or f
    pick, kind = (native, "native-cp314t") if native else (pure, "pure") if pure else (sdist, "sdist")
    if not pick:
        summary["MISSING"].append(f"{name}=={ver} (no artifact)")
        continue
    summary[kind].append(f"{name}=={ver}")
    sources.append({
        "type": "file",
        "url": pick["url"],
        "sha256": pick["digests"]["sha256"],
        "dest": "pip-cache",
    })

missing_names = {m.split("==")[0] for m in summary["MISSING"]}
install_list = " ".join(f"{n}=={v}" for n, v in entries if n not in missing_names)
module = {
    "name": "python-deps",
    "buildsystem": "simple",
    "build-commands": [
        "python3.14t -m pip install --no-index --no-build-isolation "
        f"--find-links=pip-cache --prefix=/app {install_list}"
    ],
    "sources": sources,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(module, indent=4))

print(f"wrote {OUT} ({len(sources)} sources)")
for kind in ("native-cp314t", "pure", "sdist", "MISSING"):
    print(f"[{kind}] {len(summary[kind])}")
    for item in summary[kind]:
        print(f"  {item}")
if summary["MISSING"]:
    sys.exit(1)
if summary["sdist"]:
    print("WARNING: sdist fallbacks present - these must compile inside flatpak-builder")
