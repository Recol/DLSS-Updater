# DLSS Updater

[![CodeQL](https://github.com/Recol/DLSS-Updater/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Recol/DLSS-Updater/actions?query=workflow%3ACodeQL)
![Version](./version.svg)
![Downloads](https://img.shields.io/badge/Downloads-23309-blue)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-donate-yellow.svg)](https://buymeacoffee.com/decouk)


What if you could update all the DLSS DLLs for the games detected on your system?

## Features

- Supports updating games from the following launchers:
  - Steam
  - Ubisoft
  - EA Play
  - Epic Games Launcher
  - GOG Galaxy
  - Battle.net (Note for Battle.net: Please ensure that the launcher is **open** before updating this launcher (this does not apply if you are entering a custom folder))
- A soft backup system for allowing restoration with [DLSS Swapper](https://github.com/beeradmoore/dlss-swapper).
- Support for updating Ray Reconstruction and Frame Generation DLL's.
- A GUI!
- (Some) support for manual folder locations (just insert your manual folders in place of the launcher ones).

The current supported DLL included is version 3.8.10.

## Whitelisted Games

The list of games that are not supported (whitelisted) is now maintained in a separate repository as a CSV file. This allows for easier updates and potential future expansion of game-specific information without requiring changes to the main application. You can view the current list of whitelisted games here:

[DLSS-Updater-Whitelist](https://github.com/Recol/DLSS-Updater-Whitelist/blob/main/whitelist.csv)

The whitelist includes games that:
- Use their own version of DLSS for testing (e.g., 3DMark)
- Replace the DLL when the game is booted (e.g., Warframe)
- Are using a DLSS version <2.0 (these are non-updatable)
- Have specific compatibility issues with updated DLSS versions

The application automatically fetches this CSV when run, ensuring it always has the most up-to-date information on which games to skip during the update process.

## Restoring modified DLL's
- In order to do this, simply rename the **.dlsss** file of the respective DLL which you wish to restore to **.dll** and overwrite the original file. 
- Support does not exist yet for being able to restore them using the program.

## Execution Instructions

### Running the Pre-built Application

1. Download the latest release from the [Releases](https://github.com/Recol/DLSS-Updater/releases) page.
2. Extract the downloaded folder.
3. Navigate to the `dist/DLSS_Updater` directory.
4. Run the `DLSS_Updater.exe` executable.
5. Follow the instructions in the terminal.

## Chocolatey

1. Download DLSS Updater from [here](https://community.chocolatey.org/packages/dlss-updater/).


### Building from Source

If you prefer to build the application yourself, follow these steps:

#### Prerequisites

- Python 3.6 or higher
- Git
- pip (Python package installer)

#### Steps

1. **Clone the Repository:**

    ```sh
    git clone https://github.com/Recol/DLSS-Updater.git
    cd DLSS-Updater
    ```

2. **Create and Activate a Virtual Environment:**

    ```sh
    python -m venv venv
    venv\Scripts\activate
    ```

3. **Install Dependencies:**

    ```sh
    pip install -r requirements.txt
    ```

4. **Build the Executable:**

    Ensure you have `pyinstaller` installed:

    ```sh
    pip install pyinstaller
    ```

    Run PyInstaller to build the executable:

    ```sh
    pyinstaller DLSS_Updater.spec
    ```

5. **Run the Built Executable:**

    Navigate to the `dist` directory:

    ```sh
    cd dist/DLSS_Updater
    ```

    Run the `DLSS_Updater.exe` executable:

    ```sh
    .\DLSS_Updater.exe
    ```

## Easy Anti Cheat
- The tool will not globally block games as some games do allow for this, with that being said i will whitelist games as they appear if they do not function for this.

## Future Features

- Ability to restore and ~~create backups of DLL swaps~~. - This is currently being worked on, however support has been added for restoring with DLSS Swapper currently.
- Support for a [database](https://github.com/Recol/DLSS-Updater/issues/9).
- Insert xyz feature that wants to be requested.
- Automation support with external software.

## Release Notes

Each release includes detailed notes about new features, bug fixes, and other changes. You can find the release notes in the `release_notes.txt` file included with the application or in the Releases section.

## Troubleshooting

If you encounter any issues, please refer to the Issues section on GitHub to see if your problem has already been reported. If not, feel free to open a new issue with detailed information about the problem.

## License

This project is licensed under the GNU Affero General Public License. See the LICENSE file for more details.

## Credits

This project uses Nvidia's DLSS (Deep Learning Super Sampling) technology. Please refer to Nvidia's [DLSS page](https://www.nvidia.com/en-us/geforce/technologies/dlss/) for more information. Special thanks to all contributors of open-source libraries used in this project, including but not limited to pefile, psutil, and packaging. If any are not credited and should be, please inform the author and credit will be applied where required.
