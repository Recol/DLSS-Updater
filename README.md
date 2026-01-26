# DLSS Updater

[![CodeQL](https://github.com/Recol/DLSS-Updater/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Recol/DLSS-Updater/actions?query=workflow%3ACodeQL)
[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://codspeed.io/Recol/DLSS-Updater?utm_source=badge)
![Version](./version.svg)
![Downloads](https://img.shields.io/github/downloads/Recol/DLSS-Updater/total?color=blue&label=Downloads)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-donate-yellow.svg)](https://buymeacoffee.com/decouk)


![dlss_updater](https://github.com/user-attachments/assets/b7d7fb4d-e204-412d-8e92-61a7173abfaf)

What if you could update all the DLSS/XeSS/FSR DLLs for the games detected on your system?

## Features

- **Cross-Platform Support:** Works on both Windows and Linux
- Supports updating games from the following launchers:
  - Steam (including Proton games on Linux)
  - Ubisoft
  - EA Play
  - Xbox Game Pass (PC) - Windows only
  - Epic Games Launcher
  - GOG Galaxy
  - Battle.net (Note for Battle.net: Please ensure that the launcher is **open** before updating this launcher (this does not apply if you are entering a custom folder))
- **Linux Support:**
  - Scans Steam Proton prefixes for Windows games
  - Supports Wine prefixes (Lutris, standalone Wine)
  - Automatic Steam path detection on Linux
  - Custom folder support for any game location
  - Enable the DLSS Debug Overlay
- **DLSS SR Preset Override:**
  - **This is currently bugged within the Nvidia driver, not the software, if it doesn't apply, use the Nvidia App for now**
  - Configure DLSS Super Resolution presets (K/L/M) with GPU-based recommendations
  - RTX 20/30 → Preset K recommended, RTX 40/50 → Preset M or K
  - Preset L is heavier and may reduce performance
  - Windows: System-wide registry override for all games
  - Linux: Generate Steam launch options with copy-to-clipboard
- A built in backup system for restoring game binaries if needed.
- Support for updating Ray Reconstruction/Frame Generation/Streamline (Reflex Low Latency etc) DLL's.
- Support for updating XeSS/FSR/DirectStorage DLL's (DirectStorage is Windows-only).
- A GUI!
- Support for manual folder locations.
- Backups of updated games to be restored.
- Individual game updates for specific binaries.


The current supported DLL included is DLSS 4.5 (version 3.10.5), and DLSS 4.5 for FG/RR (version 3.10.5).
The current supported DLL included is FSR 4 (version 4.0.2.0).
The current supported XeSS DLLs include XeSS 2.0.2, XeSS Frame Generation 1.2.2, and XeLL 1.2.1. Please see the [Intel XeSS releases](https://github.com/intel/xess/releases) for game support details.

## GUI
<img width="1097" height="716" alt="image" src="https://github.com/user-attachments/assets/59732e2c-add3-4ad2-ac85-0e0fed6e7ee9" />




## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Recol/DLSS-Updater&type=date&legend=top-left)](https://www.star-history.com/#Recol/DLSS-Updater&type=date&legend=top-left)

## Blacklisted Games

The list of games that are not supported (blacklisted) is now maintained in a separate repository as a CSV file. This allows for easier updates and potential future expansion of game-specific information without requiring changes to the main application. You can view the current list of blacklisted games here:

[DLSS-Updater-Blacklist](https://github.com/Recol/DLSS-Updater-Whitelist/blob/main/whitelist.csv)

The blacklist includes games that:
- Use their own version of DLSS for testing (e.g., 3DMark)
- Replace the DLL when the game is booted (e.g., Warframe)
- Are using a DLSS version <2.0 (these are non-updatable)
- Have specific compatibility issues with updated DLSS versions

The games that are blacklisted can be disabled manually by clicking the "Manage Blacklist" button in the GUI. This will allow you to skip games for whatever reason.

## Restoring modified DLL's
- You can find these in the Backups tab.

## Execution Instructions

### Windows

#### Running the Pre-built Application

1. Download the latest release from the [Releases](https://github.com/Recol/DLSS-Updater/releases) page.
2. Extract the downloaded `DLSS.Updater.X.Y.Z.zip` file.
3. Run the `DLSS_Updater.exe` executable as an administrator.

#### Winget

```sh
winget install "DLSS Updater"
```

#### Chocolatey

Download DLSS Updater from [Chocolatey](https://community.chocolatey.org/packages/dlss-updater/).

### Linux

#### Flatpak (Recommended)

**Prerequisites:** If you don't have Flatpak installed:
```sh
# Ubuntu/Debian
sudo apt install flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

# Fedora (pre-installed)

# Arch
sudo pacman -S flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

**Install from Flathub:**
```sh
flatpak install flathub io.github.recol.dlss-updater
flatpak run io.github.recol.dlss-updater
```

**Or download from GitHub Releases:**
```sh
# Download DLSS_Updater-X.Y.Z.flatpak from the Releases page, then:
flatpak install --user DLSS_Updater-X.Y.Z.flatpak
flatpak run io.github.recol.dlss-updater
```

**Uninstall:**
```sh
flatpak uninstall io.github.recol.dlss-updater
```

**libmpv Issue**
If you receive an issue associated with libmpv, this is an issue with your distro not bundling the library, please see here: https://github.com/Recol/DLSS-Updater/issues/125 for guidance.

#### Custom Game Directories

The Flatpak has read-only access to common game locations by default:
- `~/` (home directory)
- `/mnt/` (secondary drives)
- `/media/` (mounted drives)
- `/run/media/` (removable media)

To grant access to additional directories:
```sh
flatpak override --user --filesystem=/path/to/games io.github.recol.dlss-updater
```

Or use [Flatseal](https://flathub.org/apps/com.github.tchx84.Flatseal) for a graphical interface to manage permissions.

#### Linux Notes

- **Steam Proton Games:** Auto-detects Proton prefixes at `~/.steam/steam/steamapps/compatdata/`.
- **Wine Games:** Scans `~/.wine/` and Lutris games at `~/Games/`.
- **Custom Paths:** Use `flatpak override` or the in-app dialog to grant access to game directories outside the sandbox.
- **Windows-Only Features:** DLSS Debug Overlay and DirectStorage updates are disabled on Linux (shown as grayed out with tooltips).
- **Logs:** Application logs are stored at `~/.local/share/dlss-updater/dlss_updater.log`.


### Building from Source

If you prefer to build the application yourself, follow these steps:

#### Prerequisites

- Python 3.14 or higher (free-threaded Python version recommended)
- Git
- uv (Python package installer)

#### Steps (Windows)

1. **Clone the Repository:**

    ```sh
    git clone https://github.com/Recol/DLSS-Updater.git
    cd DLSS-Updater
    ```

2. **Install Dependencies:**

    ```sh
    uv sync --frozen
    ```

3. **Build the Executable:**

    ```sh
    uv run pyinstaller DLSS_Updater.spec
    ```

4. **Run the Built Executable:**

    ```sh
    .\dist\DLSS_Updater.exe
    ```

#### Steps (Linux)

1. **Clone the Repository:**

    ```sh
    git clone https://github.com/Recol/DLSS-Updater.git
    cd DLSS-Updater
    ```

2. **Install Dependencies:**

    ```sh
    uv sync --frozen
    ```

3. **Build the Executable:**

    ```sh
    uv run pyinstaller DLSS_Updater_Linux.spec, or use the flatpak build file:
    ./build_flatpak.sh
    ```

4. **Run the Built Executable:**

    ```sh
    ./dist/DLSS_Updater
    ```

## Easy Anti Cheat
- The tool will not globally block games as some games do allow for this, with that being said i will whitelist games as they appear if they do not function for this.

## Future Features

- Insert xyz feature that wants to be requested.
- Automation support with external software.

## Release Notes

Each release includes detailed notes about new features, bug fixes, and other changes. You can find the release notes in the `release_notes.txt` file included with the application or in the Releases section.

## Troubleshooting

If you encounter any issues, please refer to the Issues section on GitHub to see if your problem has already been reported. If not, feel free to open a new issue with detailed information about the problem.

## License

This project is licensed under the GNU Affero General Public License. See the LICENSE file for more details.

## Credits

This project uses Nvidia's DLSS (Deep Learning Super Sampling) technology. Please refer to Nvidia's [DLSS page](https://www.nvidia.com/en-us/geforce/technologies/dlss/) for more information. Special thanks to all contributors of open-source libraries used in this project, including but not limited to pefile, psutil, Pyinstaller and packaging. If any are not credited and should be, please inform the author and credit will be applied where required.

This project also uses Intel's XESS (Xe Super Sampling) technology. Please refer to Intel's [XESS page](https://www.intel.com/content/www/us/en/content-details/726651/intel-xe-super-sampling-xess-an-ai-based-upscaling-for-real-time-rendering.html?wapkw=xess) for more information.
