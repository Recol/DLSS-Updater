<div align="center">

# DLSS Updater

**Update every DLSS / XeSS / FSR DLL across all the games on your system — from one place.**

[![CodeQL](https://github.com/Recol/DLSS-Updater/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Recol/DLSS-Updater/actions?query=workflow%3ACodeQL)
[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://codspeed.io/Recol/DLSS-Updater?utm_source=badge)
![Version](./version.svg)
![Downloads](https://img.shields.io/github/downloads/Recol/DLSS-Updater/total?color=blue&label=Downloads)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-donate-yellow.svg)](https://buymeacoffee.com/decouk)

![Python](https://img.shields.io/badge/Python-3.14_free--threaded-3776AB?logo=python&logoColor=white)
![Flet](https://img.shields.io/badge/UI-Flet-00C4B3)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-555?logo=windows&logoColor=white)
[![License](https://img.shields.io/badge/License-AGPL_v3-orange.svg)](LICENSE)

<a href="https://trendshift.io/repositories/11650?utm_source=repository-badge&utm_medium=badge&utm_campaign=badge-repository-11650" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/11650" alt="Recol%2FDLSS-Updater | Trendshift" width="250" height="55"/></a>&nbsp;<a href="https://trendshift.io/repositories/11650?utm_source=trendshift-badge&utm_medium=badge&utm_campaign=badge-trendshift-11650" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/11650/daily?language=Python" alt="Recol%2FDLSS-Updater | Trendshift" width="250" height="55"/></a>

![dlss_updater](https://github.com/user-attachments/assets/b7d7fb4d-e204-412d-8e92-61a7173abfaf)


</div>

> [!TIP]
> **Quick start (Windows):** `winget install "DLSS Updater"` — then launch and hit scan.
> Prefer Linux? Grab the [Flatpak](#linux). Full options are in [Installation](#installation).

---

## Contents

- [Overview](#overview)
- [Features](#features)
- [Bundled DLL Versions](#bundled-dll-versions)
- [Installation](#installation)
  - [Windows](#windows)
  - [Linux](#linux)
  - [Building from Source](#building-from-source)
- [Blacklisted Games](#blacklisted-games)
- [Anti-Cheat](#anti-cheat)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [License & Credits](#license--credits)

---

## Overview

DLSS Updater scans the games installed on your system, detects the upscaling and
frame-generation DLLs they ship with, and replaces them with newer versions — with![Uploading 616464076-cbc88e69-cf05-40f0-9398-863bf7f7d93d.png…]()

automatic backups so you can always roll back. It runs on **Windows and Linux** and
understands games from every major launcher.

<div align="center">
<img width="1084" alt="DLSS Updater - Games view" src="https://github.com/user-attachments/assets/4ee3e662-94db-467b-867e-4dff7e8579e8" />

</div>

<div align="center">

### ⭐ Star History

<a href="https://www.star-history.com/?repos=Recol%2FDLSS-Updater&type=date&legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=Recol/DLSS-Updater&type=date&theme=dark&legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=Recol/DLSS-Updater&type=date&legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/image?repos=Recol/DLSS-Updater&type=date&legend=top-left" />
  </picture>
</a>

</div>

---

## Features

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>🖥️ Cross-Platform</h3>
      Runs on <strong>Windows &amp; Linux</strong> and detects games from every major launcher — Steam, Epic, GOG, Ubisoft, EA, Battle.net &amp; Xbox.
    </td>
    <td width="33%" valign="top">
      <h3>🔄 Updates Everything</h3>
      DLSS, Ray Reconstruction, Frame Generation, Streamline, XeSS, FSR &amp; DirectStorage — all at once, or one binary at a time.
    </td>
    <td width="33%" valign="top">
      <h3>🎛️ DLSS Presets</h3>
      Apply global or per-game SR / RR / FG preset overrides on Windows (NVIDIA), or per-title SR overrides with GPU recommendations on Linux.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <h3>🖼️ Smart Game Images</h3>
      Optional Steam Web API matching for near-perfect banners, plus custom banner &amp; name overrides that survive rescans.
    </td>
    <td valign="top">
      <h3>💾 Safe by Default</h3>
      Every binary is backed up before it's touched — restore any DLL in one click from the <strong>Backups</strong> tab.
    </td>
    <td valign="top">
      <h3>🚫 Custom Blacklist</h3>
      Skip (or force) any title with your own per-game blacklist, layered on top of the community list.
    </td>
  </tr>
</table>

<details>
<summary><h3>🖥️ Platform &amp; Launcher Support</h3></summary>

Cross-platform on **Windows and Linux**, with detection for the launchers below:

| Launcher | Windows | Linux | Notes |
|---|:---:|:---:|---|
| Steam | ✅ | ✅ | Includes Proton games on Linux |
| Epic Games Launcher | ✅ | ✅ | |
| GOG Galaxy | ✅ | ✅ | |
| Ubisoft Connect | ✅ | ✅ | |
| EA Play | ✅ | ✅ | |
| Battle.net | ✅ | ✅ | Keep the launcher **open** before updating (not required for custom folders) |
| Xbox Game Pass (PC) | ✅ | — | Windows only |
| Custom folders | ✅ | ✅ | Point at any game location manually |

**Linux specifics**

- Scans Steam Proton prefixes (`~/.steam/steam/steamapps/compatdata/`)
- Supports Wine prefixes (Lutris, standalone Wine)
- Automatic Steam path detection
- Enable the DLSS Debug Overlay

</details>

<details>
<summary><h3>🔄 DLL Updating</h3></summary>

- **DLSS** Super Resolution, plus **Ray Reconstruction**, **Frame Generation** and **Streamline** (Reflex Low Latency, etc.)
- **XeSS / XeSS Frame Generation / XeLL**
- **FSR**
- **DirectStorage** (Windows only)
- Update everything at once, or update individual binaries for a specific game

</details>

<details>
<summary><h3>🎛️ DLSS Preset Configuration</h3></summary>

- **Global presets (Windows, experimental)** — the *DLSS Settings* card on the home page applies preset overrides to every game via the NVIDIA driver base profile (the same mechanism as the NVIDIA App's global override). Shows the currently applied preset; takes effect at the next game launch.
  - Super Resolution (SR): Default / Latest / Preset J / K / L / M
  - Ray Reconstruction (RR): Default / Latest model
  - Frame Generation (FG): Default / Latest / Preset A / B
  - ⚠️ Per-game overrides set in the NVIDIA App take priority — set its DLSS override to *Default/Off* to let this global setting apply.
- **Per-game presets (Windows, NVIDIA only)** — the **DLSS Settings** action on any game (right-click a card, or its *⋮* menu) opens a per-game panel that overrides SR / RR / FG for just that title via its NVIDIA per-application driver profile — the same mechanism the NVIDIA App uses for per-game overrides. It takes priority over the global setting for that game and applies at the next launch. The action is hidden automatically on non-NVIDIA systems.
  - The game's executable is detected automatically (NVIDIA driver lookup → folder heuristic → Steam manifest); when it can't be determined, a *Change executable* file picker lets you point at the correct `.exe`. Your executable and preset choices are remembered between sessions.
  - The panel reads back the value the driver is actually applying and distinguishes NVIDIA's predefined value from your own override. *Reset to default* clears the override (reverting to the game's predefined value) in one click.
- **DLSS SR Preset Override (Linux only)** — configure SR presets (K/L/M) with GPU-based recommendations, and generate Steam launch options with copy-to-clipboard. *Preset L is heavier and may reduce performance.*

</details>

<details>
<summary><h3>🖼️ Game Images &amp; Custom Display</h3></summary>

- **Steam Web API integration (optional)** — connect your free Steam API key for near-perfect game image matching.
  - Auto-detects your Steam ID from the local installation
  - Four-tier app ID resolution: Manifest → Steam API → Store Search → FTS5 fuzzy match
  - One-click re-resolution to fix images from previous scans
- **Custom display** — override any game's banner image and display name via the pencil icon on its card.
  - Search the local Steam app list to find any game (no API key required; credentials improve results)
  - Overrides are stored separately from scanner data, so a rescan can't revert them
  - Reset to default at any time

</details>

<details>
<summary><h3>💾 Backups &amp; Safety</h3></summary>

- Built-in backup system that snapshots game binaries before modifying them
- Restore any modified DLL from the **Backups** tab
- Build your own per-game blacklist to skip titles for any reason

</details>

---

## Bundled DLL Versions

| Technology | Included version |
|---|---|
| DLSS Super Resolution | 4.5 (`3.10.6.0`) |
| DLSS FG / RR | 4.5 (`3.10.5`) |
| FSR | 4 (`4.0.2.0`) |
| XeSS | 2.0.2 |
| XeSS Frame Generation | 1.3.1 |
| XeLL | 1.3.0.5 |

> See the [Intel XeSS releases](https://github.com/intel/xess/releases) for per-game XeSS support details.

---

## Installation

### Windows

The **MSI installer** is the recommended way to install on Windows — it installs to
Program Files with a proper Add/Remove Programs entry.

**Winget** (recommended)
```sh
winget install "DLSS Updater"
```

**Chocolatey** — install from the [Chocolatey package page](https://community.chocolatey.org/packages/dlss-updater/).

**Direct download** — grab `DLSS.Updater.X.Y.Z.msi` from the [Releases](https://github.com/Recol/DLSS-Updater/releases) page and run it.

### Linux

The **Flatpak** is the recommended way to install on Linux.

<details>
<summary><strong>Install Flatpak (if you don't have it)</strong></summary>

```sh
# Ubuntu/Debian
sudo apt install flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

# Fedora — pre-installed

# Arch
sudo pacman -S flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```
</details>

**Install DLSS Updater**
```sh
# Download DLSS_Updater-X.Y.Z.flatpak from the Releases page, then:
flatpak install --user DLSS_Updater-X.Y.Z.flatpak
flatpak run io.github.recol.dlss-updater
```

**Uninstall**
```sh
flatpak uninstall io.github.recol.dlss-updater
```

<details>
<summary><strong>Custom game directories</strong></summary>

The Flatpak has read-only access to common game locations by default: `~/`, `/mnt/`,
`/media/`, and `/run/media/`.

To grant access to additional directories:
```sh
flatpak override --user --filesystem=/path/to/games io.github.recol.dlss-updater
```

Or use [Flatseal](https://flathub.org/apps/com.github.tchx84.Flatseal) for a graphical
permissions manager.
</details>

<details>
<summary><strong>Linux notes</strong></summary>

- **Steam Proton games:** auto-detected at `~/.steam/steam/steamapps/compatdata/`.
- **Wine games:** scans `~/.wine/` and Lutris games at `~/Games/`.
- **Custom paths:** use `flatpak override` or the in-app dialog to grant access outside the sandbox.
- **Windows-only features:** DLSS Debug Overlay and DirectStorage updates are disabled on Linux (grayed out with tooltips).
- **Logs:** stored at `~/.local/share/dlss-updater/dlss_updater.log`.
- **libmpv issue:** if you hit a libmpv error, your distro likely doesn't bundle the library — see [issue #125](https://github.com/Recol/DLSS-Updater/issues/125) for guidance.
</details>

### Building from Source

<details>
<summary><strong>Prerequisites & build steps</strong></summary>

**Prerequisites**
- Python 3.14 or higher (free-threaded build recommended)
- Git
- [uv](https://github.com/astral-sh/uv) (Python package installer)

**Windows**
```sh
git clone https://github.com/Recol/DLSS-Updater.git
cd DLSS-Updater
uv sync --frozen

# Build the executable
uv run pyinstaller DLSS_Updater.spec

# …or build the MSI installer
pwsh build_msi.ps1

# Run it
.\dist\DLSS_Updater.exe
```

**Linux**
```sh
git clone https://github.com/Recol/DLSS-Updater.git
cd DLSS-Updater
uv sync --frozen

# Build the executable…
uv run pyinstaller DLSS_Updater_Linux.spec
# …or build the Flatpak
./build_flatpak.sh

# Run it
./dist/DLSS_Updater
```
</details>

---

## Blacklisted Games

The list of unsupported (blacklisted) games is maintained as a CSV in a separate
repository, so it can be updated independently of the application:

➡️ [**DLSS-Updater-Blacklist**](https://github.com/Recol/DLSS-Updater-Whitelist/blob/main/whitelist.csv)

Games are blacklisted when they:

- Use their own version of DLSS for testing (e.g., 3DMark)
- Replace the DLL on launch (e.g., Warframe)
- Ship a DLSS version below 2.0 (non-updatable)
- Have known compatibility issues with updated DLSS versions

You can override the blacklist via the **Manage Blacklist** button in the GUI to skip
(or force) games for any reason.

---

## Anti-Cheat

The tool does not globally block anti-cheat games, since some allow DLL replacement.
Individual titles will be blacklisted as they're reported not to function.

---

## Troubleshooting

Check the [Issues](https://github.com/Recol/DLSS-Updater/issues) section to see if your
problem has already been reported. If not, open a new issue with as much detail as
possible. Each release also ships detailed notes in `release_notes.txt` and on the
[Releases](https://github.com/Recol/DLSS-Updater/releases) page.

---

## Roadmap

- Automation support with external software
- Community-requested features — [open an issue](https://github.com/Recol/DLSS-Updater/issues) to suggest one

---

## License & Credits

Licensed under the **GNU Affero General Public License**. See the [LICENSE](LICENSE) file
for details.

This project uses NVIDIA's **DLSS** (Deep Learning Super Sampling) — see NVIDIA's
[DLSS page](https://www.nvidia.com/en-us/geforce/technologies/dlss/) for more — and
Intel's **XeSS** (Xe Super Sampling) — see Intel's
[XeSS page](https://www.intel.com/content/www/us/en/content-details/726651/intel-xe-super-sampling-xess-an-ai-based-upscaling-for-real-time-rendering.html?wapkw=xess).

Special thanks to all contributors of the open-source libraries used here, including but
not limited to pefile, psutil, PyInstaller and packaging. If any are uncredited and
should be, please let the author know and credit will be applied.
