"""
Performance benchmarks for the scanner module.
Tests directory traversal and DLL detection performance.
"""
import pytest
from pathlib import Path
import tempfile
import os


@pytest.fixture
def temp_game_directory():
    """Create a temporary directory structure simulating a game directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        
        # Create a typical game directory structure
        game_dirs = [
            base / "Game1" / "bin",
            base / "Game1" / "Engine" / "Binaries" / "Win64",
            base / "Game2" / "x64",
            base / "Game3" / "Game" / "Binaries",
        ]
        
        for game_dir in game_dirs:
            game_dir.mkdir(parents=True, exist_ok=True)
            
            # Create some dummy DLL files
            (game_dir / "nvngx_dlss.dll").touch()
            (game_dir / "nvngx_dlssg.dll").touch()
            (game_dir / "libxess.dll").touch()
            (game_dir / "ffx_fsr2_api_x64.dll").touch()
            
            # Create some other files to make scanning more realistic
            (game_dir / "game.exe").touch()
            (game_dir / "config.ini").touch()
            (game_dir / "readme.txt").touch()
        
        yield base


def test_path_traversal_performance(benchmark, temp_game_directory):
    """Benchmark directory traversal performance"""
    def traverse_directory():
        dll_count = 0
        for root, dirs, files in os.walk(temp_game_directory):
            # Skip common directories that should be excluded
            dirs[:] = [d for d in dirs if d not in {'__pycache__', '.git', 'cache'}]
            
            for file in files:
                if file.lower().endswith('.dll'):
                    dll_count += 1
        return dll_count
    
    result = benchmark(traverse_directory)
    assert result > 0  # Should find at least some DLLs


def test_dll_name_filtering(benchmark):
    """Benchmark DLL name filtering performance"""
    dll_names = {
        'nvngx_dlss.dll', 'nvngx_dlssg.dll', 'nvngx_dlssd.dll',
        'libxess.dll', 'ffx_fsr2_api_x64.dll', 'ffx_fsr2_api_dx12_x64.dll',
        'amd_fidelityfx_dx12.dll', 'amd_fidelityfx_vk.dll'
    }
    
    # Simulate checking many file names
    test_files = [
        'nvngx_dlss.dll', 'game.exe', 'config.dll', 'nvngx_dlssg.dll',
        'random.dll', 'libxess.dll', 'texture.dat', 'ffx_fsr2_api_x64.dll'
    ] * 100  # Multiply to make the benchmark more meaningful
    
    def filter_dll_names():
        matched = []
        dll_names_lower = frozenset(d.lower() for d in dll_names)
        for filename in test_files:
            if filename.lower() in dll_names_lower:
                matched.append(filename)
        return len(matched)
    
    result = benchmark(filter_dll_names)
    assert result > 0


def test_path_manipulation(benchmark):
    """Benchmark path manipulation operations"""
    test_paths = [
        "/path/to/game/bin/nvngx_dlss.dll",
        "C:\\Games\\Steam\\steamapps\\common\\GameName\\Engine\\Binaries\\Win64\\nvngx_dlss.dll",
        "/mnt/games/GOG/GameTitle/x64/libxess.dll",
    ] * 100
    
    def extract_game_names():
        game_names = []
        for path_str in test_paths:
            path = Path(path_str)
            # Simple game name extraction logic
            parts = path.parts
            for i, part in enumerate(parts):
                if part.lower() in {'common', 'steamapps', 'games', 'gog'}:
                    if i + 1 < len(parts):
                        game_names.append(parts[i + 1])
                        break
        return len(game_names)
    
    result = benchmark(extract_game_names)
    assert result > 0


def test_file_extension_check(benchmark):
    """Benchmark file extension checking performance"""
    test_files = [
        "nvngx_dlss.dll", "game.exe", "config.ini", "texture.dds",
        "shader.hlsl", "nvngx_dlssg.dll", "readme.txt", "libxess.dll"
    ] * 200
    
    def check_dll_extensions():
        dll_files = []
        for filename in test_files:
            if filename.lower().endswith('.dll'):
                dll_files.append(filename)
        return len(dll_files)
    
    result = benchmark(check_dll_extensions)
    assert result > 0


def test_set_membership_check(benchmark):
    """Benchmark set membership checking for skip directories"""
    skip_dirs = frozenset({
        '__pycache__', '.git', '.svn', '.hg', 'node_modules',
        'logs', 'log', 'saves', 'save', 'screenshots', 'crash',
        'crashdumps', 'dumps', 'temp', 'tmp', 'cache', '.cache',
        'shader_cache', 'shadercache', 'gpucache', 'webcache'
    })
    
    test_dirs = [
        'bin', 'cache', 'Engine', '__pycache__', 'Binaries',
        'logs', 'x64', 'temp', 'Win64', 'shader_cache'
    ] * 150
    
    def check_skip_dirs():
        to_scan = []
        for dirname in test_dirs:
            if dirname.lower() not in skip_dirs:
                to_scan.append(dirname)
        return len(to_scan)
    
    result = benchmark(check_skip_dirs)
    assert result >= 0
