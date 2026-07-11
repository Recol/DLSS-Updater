#!/usr/bin/env python3
"""Trim flet's Flutter client to core-only for the Flathub build.

DLSS Updater uses no flet extension controls, so every flet_* extension is
removed from the client before building. This eliminates:
  - flet_rive  -> rive_native ships prebuilt .so's (glibc>=2.38; also a
                  Flathub "no prebuilt binaries" policy problem)
  - flet_video -> media_kit downloads a prebuilt libmpv mid-build and links
                  libmpv.so.1 (not in the freedesktop Platform)
  - flet_audio / flet_secure_storage / ... -> gstreamer / libsecret linkage

CRITICAL: core `flet` must then be added as a DIRECT dependency. The stock
pubspec reaches it only via dependency_overrides, and the extension packages
are what pull it into the real dependency graph - with them gone, the client
still compiles (overrides resolve imports) but Flutter's plugin registrar
walks only the dependency closure, so every native plugin (window_manager,
shared_preferences, url_launcher, ...) silently fails to register and the
client dies at runtime with MissingPluginException.

Usage: trim_flet_client.py <flet-client-dir>
"""
import re
import sys
from pathlib import Path

client = Path(sys.argv[1])
pubspec_path = client / "pubspec.yaml"
main_path = client / "lib" / "main.dart"

# 1) remove every "  flet_X:\n    path: ..." block (dependencies AND overrides)
pub = pubspec_path.read_text()
pub = re.sub(r"^  flet_\w+:\n    path: [^\n]+\n", "", pub, flags=re.M)

# 2) ensure core flet is a *direct* dependency (see docstring)
anchor = "dependencies:\n  flutter:\n    sdk: flutter\n"
if "  flet:\n    path: ../packages/flet" not in pub.split("dependency_overrides:")[0]:
    assert anchor in pub, "pubspec layout changed - update trim_flet_client.py"
    pub = pub.replace(anchor, anchor + "  flet:\n    path: ../packages/flet\n", 1)
pubspec_path.write_text(pub)

# 3) strip extension imports (either quote style, possibly multi-line) and
#    Extension() registrations from main.dart
main = main_path.read_text()
main = re.sub(r"import ['\"]package:flet_\w+/.*?as flet_\w+;\n", "", main, flags=re.S)
main = re.sub(r"^\s*flet_\w+\.Extension\(\),\n", "", main, flags=re.M)
main_path.write_text(main)

leftovers = re.findall(r".*flet_\w+.*", main)
if leftovers:
    print("ERROR: unhandled flet_ references remain in main.dart:")
    for l in leftovers:
        print(f"  {l.strip()}")
    sys.exit(1)
print("client trimmed: extensions removed, core flet added as direct dependency")
