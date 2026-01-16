"""
Tests for ML-based Adaptive Exposure Prediction System v2 (Database-Driven).
"""

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from unittest import mock

import pytest


class TestMLExposurePredictorV2Init:
    """Tests for MLExposurePredictorV2 initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default config."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            # Create empty database
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL
                )
            """
            )
            conn.close()

            config = {}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            assert predictor.good_brightness_min == 100
            assert predictor.good_brightness_max == 140
            assert predictor.min_samples == 10
            assert predictor.initial_trust == 0.5

    def test_init_with_custom_config(self):
        """Test initialization with custom config values."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL
                )
            """
            )
            conn.close()

            config = {
                "good_brightness_min": 95,
                "good_brightness_max": 145,
                "min_samples": 5,
                "initial_trust_v2": 0.6,
            }
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            assert predictor.good_brightness_min == 95
            assert predictor.good_brightness_max == 145
            assert predictor.min_samples == 5
            assert predictor.initial_trust == 0.6


class TestMLExposurePredictorV2Training:
    """Tests for training from database."""

    def test_train_from_database_with_good_frames(self):
        """Test training from database with valid good frames."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert good frames (brightness 100-140)
            for i in range(20):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon (day period)
                        500.0 + i,  # High lux (bucket 9-10)
                        50000,  # 50ms exposure
                        120.0 + (i - 10) * 0.5,  # Good brightness
                        80.0,
                        160.0,
                        30.0,  # Sun above horizon (day)
                    ),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            # Should have trained on the good frames
            assert len(predictor.state["lux_exposure_map"]) > 0
            assert predictor.state["training_stats"]["total_good_frames"] == 20

    def test_train_excludes_bad_frames(self):
        """Test that training excludes frames with bad brightness."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert bad frames (brightness outside 100-140, and not aurora-like)
            for _ in range(20):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",
                        500.0,
                        50000,
                        200.0,  # Overexposed - should be excluded
                        150.0,
                        250.0,
                        30.0,
                    ),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            # Should have no good frames - training_stats may be empty or have 0
            training_stats = predictor.state.get("training_stats", {})
            assert training_stats.get("total_good_frames", 0) == 0
            # And no buckets trained
            assert len(predictor.state.get("lux_exposure_map", {})) == 0


class TestMLExposurePredictorV2Prediction:
    """Tests for exposure prediction."""

    def test_predict_with_trained_model(self):
        """Test prediction with a trained model."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert good frames at known lux
            for _ in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon
                        500.0,  # Lux bucket 9
                        100000,  # 100ms
                        120.0,
                        80.0,
                        160.0,
                        30.0,  # Sun above horizon (day)
                    ),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            # Predict for similar lux (bucket 9) with sun elevation
            exp, conf = predictor.predict_optimal_exposure(500.0, sun_elevation=30.0)

            assert exp is not None
            assert exp == pytest.approx(0.1, rel=0.1)  # ~100ms
            assert conf > 0

    def test_predict_returns_none_for_unknown_bucket(self):
        """Test prediction returns None for unknown lux bucket."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )
            conn.close()

            config = {}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            # Predict for lux with no data
            exp, conf = predictor.predict_optimal_exposure(0.1)

            assert exp is None
            assert conf == 0.0


class TestMLExposurePredictorV2TimePeriods:
    """Tests for time period handling (both clock-based and solar-based)."""

    def test_get_time_period_night(self):
        """Test clock-based time period detection for night hours."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_time_period(0) == "night"
            assert predictor._get_time_period(3) == "night"
            assert predictor._get_time_period(22) == "night"

    def test_get_time_period_twilight(self):
        """Test clock-based time period detection for twilight hours."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            # Morning twilight
            assert predictor._get_time_period(6) == "twilight"
            assert predictor._get_time_period(8) == "twilight"
            # Evening twilight
            assert predictor._get_time_period(17) == "twilight"
            assert predictor._get_time_period(19) == "twilight"

    def test_get_time_period_day(self):
        """Test clock-based time period detection for day."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_time_period(10) == "day"
            assert predictor._get_time_period(12) == "day"
            assert predictor._get_time_period(14) == "day"


class TestMLExposurePredictorV2SolarPeriods:
    """Tests for solar elevation-based period detection (Arctic-aware)."""

    def test_get_solar_period_deep_night(self):
        """Test solar period for deep night (sun < -12°)."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_solar_period(-20) == "night"
            assert predictor._get_solar_period(-15) == "night"
            assert predictor._get_solar_period(-50) == "night"

    def test_get_solar_period_twilight(self):
        """Test solar period for twilight (-12° to 0°)."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_solar_period(-10) == "twilight"
            assert predictor._get_solar_period(-5) == "twilight"
            assert predictor._get_solar_period(-1) == "twilight"

    def test_get_solar_period_day(self):
        """Test solar period for daytime (sun > 0°)."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_solar_period(5) == "day"
            assert predictor._get_solar_period(30) == "day"
            assert predictor._get_solar_period(60) == "day"

    def test_predict_uses_sun_elevation_when_provided(self):
        """Test that prediction uses sun_elevation over timestamp."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert frames with sun_elevation in twilight
            # Lux 75 falls into bucket 7 (50-100 range)
            for i in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon by clock
                        75.0,  # Lux bucket 7 (50-100)
                        500000,  # 500ms
                        120.0,
                        80.0,
                        160.0,
                        -5.0,  # But sun at -5° (twilight by elevation)
                    ),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            # The data should be stored under twilight period (from sun_elevation)
            # not day period (from clock time)
            assert "7_twilight" in predictor.state["lux_exposure_map"]


class TestMLExposurePredictorV2Trust:
    """Tests for trust level calculations."""

    def test_trust_level_increases_with_buckets(self):
        """Test that trust level increases with more trained buckets."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL
                )
            """
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            # Empty model
            trust_empty = predictor.get_trust_level()

            # Add buckets manually
            predictor.state["lux_exposure_map"]["5_day"] = [50000, 100]
            predictor.state["lux_exposure_map"]["6_day"] = [100000, 100]
            predictor.state["lux_exposure_map"]["7_day"] = [200000, 100]

            trust_with_buckets = predictor.get_trust_level()

            assert trust_with_buckets > trust_empty

    def test_trust_level_capped_at_max(self):
        """Test that trust level is capped at max_trust."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            # Add many buckets
            for bucket_idx in range(50):
                predictor.state["lux_exposure_map"][f"{bucket_idx}_day"] = [50000, 100]

            trust = predictor.get_trust_level()

            assert trust <= predictor.max_trust


class TestMLExposurePredictorV2Blending:
    """Tests for prediction blending."""

    def test_blend_with_formula_uses_trust(self):
        """Test that blending uses trust level correctly."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            ml_value = 1.0
            formula_value = 2.0

            blended = predictor.blend_with_formula(ml_value, formula_value)

            # Blended should be between ml and formula values
            assert ml_value <= blended <= formula_value

    def test_blend_with_none_returns_formula(self):
        """Test that blending with None ML value returns formula value."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            formula_value = 2.0

            blended = predictor.blend_with_formula(None, formula_value)

            assert blended == formula_value


class TestMLExposurePredictorV2Persistence:
    """Tests for state persistence."""

    def test_state_saved_after_training(self):
        """Test that state is saved after training."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    lux REAL,
                    exposure_time_us INTEGER,
                    brightness_mean REAL,
                    brightness_p5 REAL,
                    brightness_p95 REAL,
                    sun_elevation REAL
                )
            """
            )

            # Insert good frames
            for _ in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95, sun_elevation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    ("2026-01-11T12:00:00", 500.0, 100000, 120.0, 80.0, 160.0, 30.0),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5, "state_file_v2": "test_state.json"}
            _ = MLExposurePredictorV2(
                db_path, config, state_dir=tmpdir
            )  # Creates state file as side effect

            state_file = os.path.join(tmpdir, "test_state.json")
            assert os.path.exists(state_file)

    def test_state_loaded_on_init(self):
        """Test that existing state is loaded on initialization."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            # Create existing state
            state = {
                "lux_exposure_map": {"5_day": [100000, 50]},
                "percentile_thresholds": {},
                "training_stats": {"total_good_frames": 50},
                "last_trained": datetime.now().isoformat(),
                "version": 2,
            }
            state_file = os.path.join(tmpdir, "ml_state_v2.json")
            with open(state_file, "w") as f:
                json.dump(state, f)

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert "5_day" in predictor.state["lux_exposure_map"]
            assert predictor.state["training_stats"]["total_good_frames"] == 50


class TestMLExposurePredictorV2BucketInterpolation:
    """Tests for bucket interpolation to fill data gaps."""

    def test_find_adjacent_buckets_both_sides(self):
        """Test finding adjacent buckets with data on both sides."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)
            # Manually add buckets at positions 3 and 7
            predictor.state["lux_exposure_map"]["3_day"] = [500000, 50]
            predictor.state["lux_exposure_map"]["7_day"] = [50000, 50]

            # Query for bucket 5 (between 3 and 7)
            lower, upper = predictor._find_adjacent_buckets(15.0, "day")  # ~bucket 5

            assert lower == 3
            assert upper == 7

    def test_find_adjacent_buckets_only_lower(self):
        """Test finding adjacent buckets with only lower bucket available."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)
            # Only add bucket at position 3
            predictor.state["lux_exposure_map"]["3_day"] = [500000, 50]

            # Query for bucket 5
            lower, upper = predictor._find_adjacent_buckets(15.0, "day")

            assert lower == 3
            assert upper is None

    def test_interpolate_between_buckets_returns_interpolated_value(self):
        """Test that interpolation returns value between adjacent buckets."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)
            # Add buckets with known exposures
            # Bucket 3: lux ~2.0, exposure 1s
            # Bucket 7: lux ~50.0, exposure 0.1s
            predictor.state["lux_exposure_map"]["3_day"] = [1000000, 100]  # 1s
            predictor.state["lux_exposure_map"]["7_day"] = [100000, 100]  # 0.1s

            # Query for lux 10 (between bucket 3 and 7)
            result = predictor._interpolate_between_buckets(10.0, "day")

            assert result is not None
            exp_seconds, confidence = result
            # Interpolated exposure should be between 0.1s and 1s
            assert 0.1 < exp_seconds < 1.0
            # Confidence should be reduced for interpolation
            assert confidence < 0.8

    def test_interpolate_returns_none_without_adjacent_data(self):
        """Test that interpolation returns None when no adjacent buckets."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)
            # No buckets at all

            result = predictor._interpolate_between_buckets(10.0, "day")

            assert result is None

    def test_predict_uses_interpolation_when_exact_missing(self):
        """Test that predict_optimal_exposure uses interpolation for missing buckets."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)
            # Add surrounding buckets but not the exact one
            predictor.state["lux_exposure_map"]["3_day"] = [1000000, 100]  # 1s at ~2 lux
            predictor.state["lux_exposure_map"]["7_day"] = [100000, 100]  # 0.1s at ~50 lux

            # Query for lux=10 (bucket 5, which is missing)
            exp_seconds, confidence = predictor.predict_optimal_exposure(
                10.0, sun_elevation=30.0  # Day period
            )

            # Should get interpolated result
            assert exp_seconds is not None
            assert 0.1 < exp_seconds < 1.0
            assert confidence > 0  # Should have some confidence

    def test_interpolation_extrapolates_with_single_adjacent_bucket(self):
        """Test that single adjacent bucket provides reduced confidence prediction."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL, sun_elevation REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)
            # Only add one nearby bucket
            predictor.state["lux_exposure_map"]["3_day"] = [1000000, 100]  # 1s

            result = predictor._interpolate_between_buckets(10.0, "day")

            assert result is not None
            exp_seconds, confidence = result
            # Should use nearest bucket value
            assert exp_seconds == pytest.approx(1.0, rel=0.01)
            # Confidence should be heavily reduced for extrapolation
            assert confidence < 0.5
