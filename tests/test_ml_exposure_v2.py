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
                    brightness_p95 REAL
                )
            """
            )

            # Insert good frames (brightness 100-140)
            for i in range(20):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon (day period)
                        500.0 + i,  # High lux (bucket 9-10)
                        50000,  # 50ms exposure
                        120.0 + (i - 10) * 0.5,  # Good brightness
                        80.0,
                        160.0,
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
                    brightness_p95 REAL
                )
            """
            )

            # Insert bad frames (brightness outside 100-140)
            for i in range(20):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",
                        500.0,
                        50000,
                        200.0,  # Overexposed - should be excluded
                        150.0,
                        250.0,
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
                    brightness_p95 REAL
                )
            """
            )

            # Insert good frames at known lux
            for i in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        "2026-01-11T12:00:00",  # Noon
                        500.0,  # Lux bucket 9
                        100000,  # 100ms
                        120.0,
                        80.0,
                        160.0,
                    ),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

            # Predict for similar lux (bucket 9)
            exp, conf = predictor.predict_optimal_exposure(500.0)

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
                    brightness_p95 REAL
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
    """Tests for time period handling."""

    def test_get_time_period_night(self):
        """Test time period detection for night hours."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_time_period(0) == "night"
            assert predictor._get_time_period(3) == "night"
            assert predictor._get_time_period(22) == "night"

    def test_get_time_period_morning_transition(self):
        """Test time period detection for morning transition."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_time_period(6) == "morning_transition"
            assert predictor._get_time_period(8) == "morning_transition"

    def test_get_time_period_day(self):
        """Test time period detection for day."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_time_period(10) == "day"
            assert predictor._get_time_period(12) == "day"

    def test_get_time_period_evening_transition(self):
        """Test time period detection for evening transition."""
        from src.ml_exposure_v2 import MLExposurePredictorV2

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY, timestamp TEXT, lux REAL, exposure_time_us INTEGER, brightness_mean REAL, brightness_p5 REAL, brightness_p95 REAL)"
            )
            conn.close()

            predictor = MLExposurePredictorV2(db_path, {}, state_dir=tmpdir)

            assert predictor._get_time_period(14) == "evening_transition"
            assert predictor._get_time_period(18) == "evening_transition"


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
            for i in range(50):
                predictor.state["lux_exposure_map"][f"{i}_day"] = [50000, 100]

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
                    brightness_p95 REAL
                )
            """
            )

            # Insert good frames
            for i in range(15):
                conn.execute(
                    """
                    INSERT INTO captures (timestamp, lux, exposure_time_us, brightness_mean, brightness_p5, brightness_p95)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    ("2026-01-11T12:00:00", 500.0, 100000, 120.0, 80.0, 160.0),
                )
            conn.commit()
            conn.close()

            config = {"min_samples": 5, "state_file_v2": "test_state.json"}
            predictor = MLExposurePredictorV2(db_path, config, state_dir=tmpdir)

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
