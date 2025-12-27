"""
Minimal smoke tests for Python 3.14 free-threading compatibility.
Tests thread-safe singletons and caches under concurrent access.
"""

import sys
import threading
import concurrent.futures
from unittest.mock import patch

import pytest


# Check if we're running on free-threaded Python
FREE_THREADING = getattr(sys.flags, 'free_threading', False)


class TestConfigManagerThreadSafety:
    """Test ConfigManager singleton thread-safety"""

    def test_singleton_concurrent_access(self, tmp_path):
        """Ensure ConfigManager returns same instance from multiple threads"""
        # Patch the config path to use temp directory
        config_file = tmp_path / "config.ini"

        with patch('dlss_updater.config.get_config_path', return_value=str(config_file)):
            # Force re-import to test fresh singleton
            import importlib
            import dlss_updater.config as config_module
            config_module.ConfigManager._instance = None

            instances = []
            errors = []

            def get_instance():
                try:
                    from dlss_updater.config import ConfigManager
                    instance = ConfigManager()
                    instances.append(id(instance))
                except Exception as e:
                    errors.append(e)

            # Create multiple threads trying to get the singleton
            threads = [threading.Thread(target=get_instance) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All threads should get the same instance
            assert not errors, f"Errors occurred: {errors}"
            assert len(set(instances)) == 1, "Multiple singleton instances created!"


class TestDatabaseManagerThreadSafety:
    """Test DatabaseManager singleton thread-safety"""

    def test_singleton_concurrent_access(self, tmp_path):
        """Ensure DatabaseManager returns same instance from multiple threads"""
        db_file = tmp_path / "test.db"

        with patch('dlss_updater.database.get_db_path', return_value=str(db_file)):
            import dlss_updater.database as db_module
            db_module.DatabaseManager._instance = None

            instances = []
            errors = []

            def get_instance():
                try:
                    from dlss_updater.database import DatabaseManager
                    instance = DatabaseManager()
                    instances.append(id(instance))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=get_instance) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"Errors occurred: {errors}"
            assert len(set(instances)) == 1, "Multiple singleton instances created!"


class TestParseVersionCacheThreadSafety:
    """Test parse_version cache thread-safety"""

    def test_concurrent_cache_access(self):
        """Test parse_version cache under concurrent access"""
        from dlss_updater.updater import parse_version

        versions = [
            "1.0.0.0", "2.5.3.1", "310.2.1.0", "1.2.2504.401",
            "2.7.30.0", "4.0.2.0", "0.0.0.1", "999.999.999.999"
        ]
        results = {}
        errors = []

        def parse_versions():
            try:
                for v in versions:
                    result = parse_version(v)
                    key = (v, result)
                    results.setdefault(v, set()).add(result)
            except Exception as e:
                errors.append(e)

        # Run multiple threads parsing the same versions
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(parse_versions) for _ in range(50)]
            concurrent.futures.wait(futures)

        assert not errors, f"Errors occurred: {errors}"
        # Each version should produce consistent results
        for v, result_set in results.items():
            assert len(result_set) == 1, f"Inconsistent results for {v}: {result_set}"


class TestNormalizeGameNameCacheThreadSafety:
    """Test normalize_game_name cache thread-safety"""

    def test_concurrent_cache_access(self):
        """Test normalize_game_name cache under concurrent access"""
        from dlss_updater.steam_integration import SteamIntegration

        game_names = [
            "The Witcher 3: Wild Hunt",
            "Cyberpunk 2077™",
            "DOOM Eternal®",
            "Half-Life 2",
            "Counter-Strike 2",
            "Baldur's Gate 3",
        ]
        results = {}
        errors = []

        def normalize_names():
            try:
                for name in game_names:
                    result = SteamIntegration.normalize_game_name(name)
                    results.setdefault(name, set()).add(result)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(normalize_names) for _ in range(50)]
            concurrent.futures.wait(futures)

        assert not errors, f"Errors occurred: {errors}"
        for name, result_set in results.items():
            assert len(result_set) == 1, f"Inconsistent results for {name}: {result_set}"


class TestWhitelistCacheThreadSafety:
    """Test whitelist cache thread-safety"""

    def test_sync_access_thread_safety(self):
        """Test get_whitelist_sync under concurrent access"""
        from dlss_updater.whitelist import get_whitelist_sync

        results = []
        errors = []

        def get_whitelist():
            try:
                result = get_whitelist_sync()
                results.append(type(result).__name__)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_whitelist) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors occurred: {errors}"
        # All results should be sets
        assert all(r == 'set' for r in results), f"Unexpected types: {results}"


@pytest.mark.skipif(not FREE_THREADING, reason="Only runs on free-threaded Python")
class TestFreeThreadingSpecific:
    """Tests specific to free-threaded Python 3.14+"""

    def test_free_threading_enabled(self):
        """Verify free-threading is actually enabled"""
        assert FREE_THREADING, "Free-threading should be enabled"
        assert not hasattr(sys, '_is_gil_enabled') or not sys._is_gil_enabled()
