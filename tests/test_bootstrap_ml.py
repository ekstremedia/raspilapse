"""
Tests for ML Bootstrap Script.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest import mock

import pytest


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_config(self):
        """Test loading a valid config file."""
        from src.bootstrap_ml import load_config

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("system:\n  output_directory: /tmp/test\n")
            f.flush()
            config = load_config(f.name)
            assert config["system"]["output_directory"] == "/tmp/test"
            os.unlink(f.name)

    def test_load_nonexistent_config(self):
        """Test loading a nonexistent config file raises error."""
        from src.bootstrap_ml import load_config

        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yml")


class TestFindMetadataFiles:
    """Tests for find_metadata_files function."""

    def test_find_files_empty_directory(self):
        """Test finding files in empty directory."""
        from src.bootstrap_ml import find_metadata_files

        with tempfile.TemporaryDirectory() as tmpdir:
            start = datetime.now() - timedelta(days=1)
            end = datetime.now()
            files = find_metadata_files(tmpdir, start, end)
            assert files == []

    def test_find_files_with_metadata(self):
        """Test finding metadata files."""
        from src.bootstrap_ml import find_metadata_files

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create directory structure
            now = datetime.now()
            date_dir = os.path.join(
                tmpdir,
                str(now.year),
                f"{now.month:02d}",
                f"{now.day:02d}",
            )
            os.makedirs(date_dir)

            # Create metadata files
            for i in range(3):
                filepath = os.path.join(date_dir, f"frame_{i:04d}_metadata.json")
                with open(filepath, "w") as f:
                    json.dump({"test": i}, f)

            start = now - timedelta(hours=1)
            end = now + timedelta(hours=1)
            files = find_metadata_files(tmpdir, start, end)
            assert len(files) == 3


class TestProcessMetadataFile:
    """Tests for process_metadata_file function."""

    def test_process_valid_enriched_metadata(self):
        """Test processing valid enriched metadata."""
        from src.bootstrap_ml import process_metadata_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            metadata = {
                "ExposureTime": 5000,
                "capture_timestamp": "2026-01-10T12:00:00",
                "diagnostics": {
                    "smoothed_lux": 100.0,
                    "brightness": {"mean_brightness": 120},
                },
            }
            json.dump(metadata, f)
            f.flush()

            result = process_metadata_file(f.name)
            assert result is not None
            assert result["ExposureTime"] == 5000
            os.unlink(f.name)

    def test_process_valid_raw_metadata(self):
        """Test processing valid raw camera metadata."""
        from src.bootstrap_ml import process_metadata_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            metadata = {
                "ExposureTime": 5000,
                "Lux": 100.0,
                "capture_timestamp": "2026-01-10T12:00:00",
            }
            json.dump(metadata, f)
            f.flush()

            result = process_metadata_file(f.name)
            assert result is not None
            # Should have synthetic diagnostics added
            assert "diagnostics" in result
            assert result["diagnostics"]["raw_lux"] == 100.0
            os.unlink(f.name)

    def test_process_missing_lux(self):
        """Test processing metadata without lux returns None."""
        from src.bootstrap_ml import process_metadata_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            metadata = {
                "ExposureTime": 5000,
                "capture_timestamp": "2026-01-10T12:00:00",
            }
            json.dump(metadata, f)
            f.flush()

            result = process_metadata_file(f.name)
            assert result is None
            os.unlink(f.name)

    def test_process_invalid_json(self):
        """Test processing invalid JSON returns None."""
        from src.bootstrap_ml import process_metadata_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            f.flush()

            result = process_metadata_file(f.name)
            assert result is None
            os.unlink(f.name)

    def test_process_nonexistent_file(self):
        """Test processing nonexistent file returns None."""
        from src.bootstrap_ml import process_metadata_file

        result = process_metadata_file("/nonexistent/file.json")
        assert result is None


class TestBootstrapMl:
    """Tests for bootstrap_ml function."""

    def test_bootstrap_no_files(self):
        """Test bootstrap with no metadata files."""
        from src.bootstrap_ml import bootstrap_ml

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {}
            start = datetime.now() - timedelta(days=1)
            end = datetime.now()

            stats = bootstrap_ml(config, tmpdir, start, end, tmpdir)

            assert stats["files_found"] == 0
            assert stats["files_processed"] == 0

    def test_bootstrap_with_files(self):
        """Test bootstrap with metadata files."""
        from src.bootstrap_ml import bootstrap_ml

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create directory structure
            now = datetime.now()
            date_dir = os.path.join(
                tmpdir,
                str(now.year),
                f"{now.month:02d}",
                f"{now.day:02d}",
            )
            os.makedirs(date_dir)

            # Create metadata files with raw format
            for i in range(5):
                filepath = os.path.join(date_dir, f"frame_{i:04d}_metadata.json")
                metadata = {
                    "ExposureTime": 5000 + i * 1000,
                    "Lux": 50.0 + i * 10,
                    "capture_timestamp": now.isoformat(),
                }
                with open(filepath, "w") as f:
                    json.dump(metadata, f)

            config = {}
            start = now - timedelta(hours=1)
            end = now + timedelta(hours=1)
            output_dir = os.path.join(tmpdir, "ml_state")

            stats = bootstrap_ml(config, tmpdir, start, end, output_dir)

            assert stats["files_found"] == 5
            assert stats["files_processed"] == 5
            assert stats["files_with_errors"] == 0


class TestPrintLearnedTable:
    """Tests for print_learned_table function."""

    def test_print_table_no_state(self, capsys):
        """Test printing table when no state file exists."""
        from src.bootstrap_ml import print_learned_table

        with tempfile.TemporaryDirectory() as tmpdir:
            print_learned_table(tmpdir)
            captured = capsys.readouterr()
            assert "not found" in captured.err or captured.out == ""

    def test_print_table_with_state(self, capsys):
        """Test printing table with existing state."""
        from src.bootstrap_ml import print_learned_table

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "ml_state.json")
            state = {
                "lux_exposure_map": {"3": [0.5, 50], "7": [0.01, 100]},
                "solar_patterns": {
                    "10": {"12": {"0": 100.0, "15": 120.0}},
                },
            }
            with open(state_file, "w") as f:
                json.dump(state, f)

            print_learned_table(tmpdir)
            captured = capsys.readouterr()
            assert "LUX-EXPOSURE MAPPING" in captured.out
            assert "SOLAR PATTERNS" in captured.out


class TestMainFunction:
    """Tests for main CLI function."""

    def test_main_show_table(self):
        """Test main with --show-table flag."""
        from src.bootstrap_ml import main

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "ml_state.json")
            state = {"lux_exposure_map": {}, "solar_patterns": {}}
            with open(state_file, "w") as f:
                json.dump(state, f)

            with mock.patch("sys.argv", ["bootstrap_ml.py", "--show-table", "-o", tmpdir]):
                # Should not raise
                main()

    def test_main_missing_config(self):
        """Test main with missing config file."""
        from src.bootstrap_ml import main

        with mock.patch("sys.argv", ["bootstrap_ml.py", "-c", "/nonexistent/config.yml"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
