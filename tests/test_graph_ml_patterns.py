"""
Tests for ML Solar Patterns Graph Generation.
"""

import json
import os
import tempfile
from unittest import mock

import pytest


class TestLoadMlState:
    """Tests for load_ml_state function."""

    def test_load_valid_state(self):
        """Test loading valid ML state file."""
        from src.graph_ml_patterns import load_ml_state

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            state = {
                "solar_patterns": {"10": {"12": {"0": 100.0}}},
                "confidence": 500,
                "total_predictions": 1000,
            }
            json.dump(state, f)
            f.flush()

            result = load_ml_state(f.name)
            assert result["confidence"] == 500
            assert "10" in result["solar_patterns"]
            os.unlink(f.name)

    def test_load_nonexistent_state(self):
        """Test loading nonexistent state file exits."""
        from src.graph_ml_patterns import load_ml_state

        with pytest.raises(SystemExit):
            load_ml_state("/nonexistent/ml_state.json")


class TestDayOfYearToDate:
    """Tests for day_of_year_to_date function."""

    def test_day_1(self):
        """Test day 1 is January 1."""
        from src.graph_ml_patterns import day_of_year_to_date

        result = day_of_year_to_date(1, 2026)
        assert result.month == 1
        assert result.day == 1

    def test_day_32(self):
        """Test day 32 is February 1."""
        from src.graph_ml_patterns import day_of_year_to_date

        result = day_of_year_to_date(32, 2026)
        assert result.month == 2
        assert result.day == 1

    def test_day_365(self):
        """Test day 365 is December 31."""
        from src.graph_ml_patterns import day_of_year_to_date

        result = day_of_year_to_date(365, 2026)
        assert result.month == 12
        assert result.day == 31


class TestCreateSolarPatternGraph:
    """Tests for create_solar_pattern_graph function."""

    def test_create_graph_empty_patterns(self, capsys):
        """Test creating graph with no solar patterns."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_graph.png")
            state = {"solar_patterns": {}, "confidence": 0, "total_predictions": 0}

            create_solar_pattern_graph(state, output_path)
            captured = capsys.readouterr()
            assert "No solar patterns" in captured.out

    def test_create_graph_with_patterns(self):
        """Test creating graph with solar patterns."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_graph.png")
            state = {
                "solar_patterns": {
                    "10": {
                        "8": {"0": 10.0, "15": 15.0, "30": 20.0, "45": 25.0},
                        "9": {"0": 30.0, "15": 40.0, "30": 50.0, "45": 60.0},
                        "10": {"0": 70.0, "15": 80.0, "30": 90.0, "45": 100.0},
                        "11": {"0": 110.0, "15": 120.0, "30": 130.0, "45": 140.0},
                        "12": {"0": 150.0, "15": 160.0, "30": 170.0, "45": 180.0},
                        "13": {"0": 170.0, "15": 160.0, "30": 150.0, "45": 140.0},
                        "14": {"0": 130.0, "15": 120.0, "30": 110.0, "45": 100.0},
                    },
                    "11": {
                        "10": {"0": 80.0, "15": 90.0, "30": 100.0, "45": 110.0},
                        "11": {"0": 120.0, "15": 130.0, "30": 140.0, "45": 150.0},
                        "12": {"0": 160.0, "15": 170.0, "30": 180.0, "45": 190.0},
                    },
                },
                "confidence": 500,
                "total_predictions": 1000,
            }

            create_solar_pattern_graph(state, output_path)

            assert os.path.exists(output_path)
            # Check file size is reasonable (should be > 10KB for a graph)
            assert os.path.getsize(output_path) > 10000

    def test_create_graph_single_day(self):
        """Test creating graph with single day of patterns."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_graph.png")
            state = {
                "solar_patterns": {
                    "10": {
                        "12": {"0": 100.0, "15": 110.0, "30": 120.0, "45": 130.0},
                    },
                },
                "confidence": 100,
                "total_predictions": 200,
            }

            create_solar_pattern_graph(state, output_path)

            assert os.path.exists(output_path)

    def test_create_graph_multiple_days(self):
        """Test creating graph with multiple days shows trend line."""
        from src.graph_ml_patterns import create_solar_pattern_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_graph.png")
            # Create 5 days of data to trigger trend line
            state = {
                "solar_patterns": {
                    str(day): {
                        "10": {"0": 50.0 + day * 10},
                        "11": {"0": 100.0 + day * 10},
                        "12": {"0": 150.0 + day * 10},
                        "13": {"0": 100.0 + day * 10},
                    }
                    for day in range(3, 8)
                },
                "confidence": 500,
                "total_predictions": 1000,
            }

            create_solar_pattern_graph(state, output_path)

            assert os.path.exists(output_path)


class TestMainFunction:
    """Tests for main CLI function."""

    def test_main_with_valid_state(self):
        """Test main function with valid state file."""
        from src.graph_ml_patterns import main

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "ml_state.json")
            output_file = os.path.join(tmpdir, "output.png")

            state = {
                "solar_patterns": {
                    "10": {"12": {"0": 100.0, "15": 110.0}},
                },
                "confidence": 100,
                "total_predictions": 200,
            }
            with open(state_file, "w") as f:
                json.dump(state, f)

            with mock.patch(
                "sys.argv", ["graph_ml_patterns.py", "-s", state_file, "-o", output_file]
            ):
                main()

            assert os.path.exists(output_file)

    def test_main_creates_output_dir(self):
        """Test main function creates output directory if needed."""
        from src.graph_ml_patterns import main

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "ml_state.json")
            output_file = os.path.join(tmpdir, "subdir", "output.png")

            state = {
                "solar_patterns": {"10": {"12": {"0": 100.0}}},
                "confidence": 0,
                "total_predictions": 0,
            }
            with open(state_file, "w") as f:
                json.dump(state, f)

            with mock.patch(
                "sys.argv", ["graph_ml_patterns.py", "-s", state_file, "-o", output_file]
            ):
                main()

            assert os.path.exists(output_file)
