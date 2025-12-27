"""
Performance benchmarks for utility functions.
Tests string operations, path handling, and data processing.
"""
import pytest
from pathlib import Path


def test_string_comparison_performance(benchmark):
    """Benchmark case-insensitive string comparisons"""
    game_names = ["warframe", "cyberpunk2077", "3dmark", "portal2", "halflife2"] * 50
    blacklist = {"warframe", "3dmark", "benchmark"}
    
    def check_blacklist():
        matches = []
        for name in game_names:
            if any(bl in name.lower() for bl in blacklist):
                matches.append(name)
        return len(matches)
    
    result = benchmark(check_blacklist)
    assert result > 0


def test_version_string_parsing(benchmark):
    """Benchmark version string parsing"""
    version_strings = [
        "3.10.4.0", "2.5.1.0", "4.0.2.0", "1.2.3.4",
        "10.20.30.40", "0.1.2.3", "99.99.99.99"
    ] * 100
    
    def parse_versions():
        parsed = []
        for version in version_strings:
            parts = version.split('.')
            # Convert to tuple of integers for comparison
            version_tuple = tuple(int(p) for p in parts)
            parsed.append(version_tuple)
        return len(parsed)
    
    result = benchmark(parse_versions)
    assert result == len(version_strings)


def test_path_normalization(benchmark):
    """Benchmark path normalization"""
    paths = [
        "C:\\Games\\Steam\\steamapps\\common\\Game\\bin\\nvngx_dlss.dll",
        "/home/user/games/game1/lib/libxess.dll",
        "D:\\Program Files\\Epic Games\\GameName\\Engine\\Binaries\\Win64\\nvngx_dlss.dll",
"/mnt/storage/games/GOG/Game2/x64/ffx_fsr2_api_x64.dll"
    ] * 100
    
    def normalize_paths():
        normalized = []
        for path_str in paths:
            # Normalize path separators and case
            path = Path(path_str)
            normalized.append(str(path.as_posix()))
        return len(normalized)
    
    result = benchmark(normalize_paths)
    assert result == len(paths)


def test_file_size_calculation(benchmark):
    """Benchmark file size calculations"""
    file_sizes = [
        1024 * 500,  # 500 KB
        1024 * 1024 * 2,  # 2 MB
        1024 * 1024 * 50,  # 50 MB
        1024 * 100,  # 100 KB
    ] * 200
    
    def format_file_sizes():
        formatted = []
        for size in file_sizes:
            # Convert to human-readable format
            if size < 1024:
                formatted.append(f"{size} B")
            elif size < 1024 * 1024:
                formatted.append(f"{size / 1024:.2f} KB")
            else:
                formatted.append(f"{size / (1024 * 1024):.2f} MB")
        return len(formatted)
    
    result = benchmark(format_file_sizes)
    assert result == len(file_sizes)


def test_dll_type_mapping(benchmark):
    """Benchmark DLL type identification"""
    dll_names = [
        "nvngx_dlss.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll",
        "libxess.dll", "ffx_fsr2_api_x64.dll", "ffx_fsr2_api_dx12_x64.dll",
        "amd_fidelityfx_dx12.dll", "unknown.dll"
    ] * 150
    
    dll_type_map = {
        'nvngx_dlss.dll': 'DLSS',
        'nvngx_dlssg.dll': 'DLSS Frame Generation',
        'nvngx_dlssd.dll': 'DLSS Ray Reconstruction',
        'libxess.dll': 'XeSS',
        'ffx_fsr2_api_x64.dll': 'FSR 2',
        'ffx_fsr2_api_dx12_x64.dll': 'FSR 2',
        'amd_fidelityfx_dx12.dll': 'FidelityFX',
    }
    
    def identify_dll_types():
        types = []
        for dll_name in dll_names:
            dll_type= dll_type_map.get(dll_name.lower(), 'Unknown')
            types.append(dll_type)
        return len(types)
    
    result = benchmark(identify_dll_types)
    assert result == len(dll_names)


def test_list_filtering_performance(benchmark):
    """Benchmark filtering large lists"""
    # Simulate filtering a large list of detected files
    all_files = []
    for i in range(1000):
        all_files.extend([
            f"game{i}.exe",
            f"nvngx_dlss_{i}.dll",
            f"config{i}.ini",
            f"libxess_{i}.dll",
            f"texture{i}.dds"
        ])
    
    def filter_dll_files():
        dll_files = [f for f in all_files if f.endswith('.dll')]
        return len(dll_files)
    
    result = benchmark(filter_dll_files)
    assert result > 0


def test_dictionary_lookup_performance(benchmark):
    """Benchmark dictionary lookups for launcher paths"""
    launcher_paths = {
        'steam': 'C:\\Program Files\\Steam',
        'epic': 'C:\\Program Files\\Epic Games',
        'gog': 'C:\\GOG Games',
        'ubisoft': 'C:\\Program Files\\Ubisoft',
        'ea': 'C:\\Program Files\\EA Games',
        'xbox': 'C:\\Program Files\\Xbox Games',
        'battlenet': 'C:\\Program Files\\Battle.net'
    }
    
    queries = ['steam', 'epic', 'gog', 'unknown', 'ubisoft', 'ea'] * 200
    
    def lookup_paths():
        found = []
        for launcher in queries:
            path = launcher_paths.get(launcher)
            if path:
                found.append(path)
        return len(found)
    
    result = benchmark(lookup_paths)
    assert result > 0
