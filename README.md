# DLSS Updater

What if you could update all the DLSS DLLs for the games detected on your system?

## Features

- Supports updating games from the following launchers:
- Steam
- Ubisoft
- EA Play
- Epic Games Launcher

The current supported DLL included is version 3.7.10.

## Whitelisted Games

The following games here are **not** supported:
- 3DMark (This is not supported as this uses it's own version for testing)
- Warframe (The WF launcher will replace the DLL when the game is booted)
- Insert xyz other game not included.

## Execution Instructions

### Running the Pre-built Application

1. Download the latest release from the [Releases](https://github.com/Recol/DLSS-Updater/releases) page.
2. Extract the downloaded folder.
3. Navigate to the `dist/DLSS_Updater` directory.
4. Run the `DLSS_Updater.exe` executable.
5. Follow the instructions in the terminal.

### Building from Source

If you prefer to build the application yourself, follow these steps:

#### Prerequisites

- Python 3.6 or higher
- Git
- pip (Python package installer)

#### Steps

1. **Clone the Repository:**  
   ```sh
   git clone https://github.com/yourusername/DLSS-Updater.git
   cd DLSS-Updater

2. **Create and Activate a Virtual Environment:**  
    python -m venv venv
    venv\Scripts\activate

3. **Install Dependencies:**  
    pip install -r requirements.txt  

4. **Build the Executable:**  
    Ensure you have pyinstaller installed:  
        pip install pyinstaller  
    Run PyInstaller to build the executable:  
    pyinstaller DLSS_Updater.spec  

5. **Run the Built Executable:**  
    Navigate to the dist directory:  
        cd dist/DLSS_Updater  
    Run the DLSS_Updater.exe executable:  
        .\DLSS_Updater.exe  



## Features which will be added in the future:
- Support for additional launchers.
- Potential support for updating the DLSS upscaler itself.
- Possible other game inclusion.
- Insert xyz feature that wants to be requested.

## Release Notes
 Each release includes detailed notes about new features, bug fixes, and other changes. 
 You can find the release notes in the release_notes.txt file included with the application or in the Releases section.

## Troubleshooting
 If you encounter any issues, please refer to the Issues section on GitHub to see if your problem has already been reported. 
 If not, feel free to open a new issue with detailed information about the problem.

## License
 This project is licensed under the Creative Commons Attribution Non Commercial Share Alike 4.0 International License. See the LICENSE file for more details.

## Credits
 This project uses Nvidia's DLSS (Deep Learning Super Sampling) technology. Please refer to Nvidia's [DLSS page](https://www.nvidia.com/en-us/geforce/technologies/dlss/) for more information.
 Special thanks to all contributors of open-source libraries used in this project, including but not limited to pefile, psutil, and packaging.
 If any are not credited and should be, please inform the author and credit will be applied where required.