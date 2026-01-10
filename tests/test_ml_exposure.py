"""
Tests for ML-based Adaptive Exposure Prediction System.
"""

import json
import os
import tempfile
import time
from unittest import mock

import pytest


class TestMLExposurePredictorInit:
    """Tests for MLExposurePredictor initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default config."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)

            assert predictor.solar_learning_rate == 0.1
            assert predictor.exposure_learning_rate == 0.05
            assert predictor.correction_learning_rate == 0.1
            assert predictor.initial_trust == 0.0
            assert predictor.max_trust == 0.8

    def test_init_with_custom_config(self):
        """Test initialization with custom config values."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "solar_learning_rate": 0.2,
                "exposure_learning_rate": 0.1,
                "initial_trust": 0.5,
                "max_trust": 0.9,
                "shadow_mode": True,
            }
            predictor = MLExposurePredictor(config, state_dir=tmpdir)

            assert predictor.solar_learning_rate == 0.2
            assert predictor.exposure_learning_rate == 0.1
            assert predictor.initial_trust == 0.5
            assert predictor.max_trust == 0.9
            assert predictor.shadow_mode is True

    def test_init_loads_existing_state(self):
        """Test that initialization loads existing state file."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create existing state
            state = {
                "confidence": 500,
                "total_predictions": 1000,
                "lux_exposure_map": {"3": [5.0, 100]},
                "solar_patterns": {},
                "correction_memory": {},
                "version": 1,
            }
            state_file = os.path.join(tmpdir, "ml_state.json")
            with open(state_file, "w") as f:
                json.dump(state, f)

            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            assert predictor.state["confidence"] == 500
            assert predictor.state["total_predictions"] == 1000


class TestLuxBuckets:
    """Tests for lux bucketing functionality."""

    def test_get_lux_bucket_very_low(self):
        """Test bucket assignment for very low lux."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            assert predictor._get_lux_bucket_index(0.1) == 0
            assert predictor._get_lux_bucket_index(0.4) == 0

    def test_get_lux_bucket_low(self):
        """Test bucket assignment for low lux."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            assert predictor._get_lux_bucket_index(0.5) == 1
            assert predictor._get_lux_bucket_index(0.9) == 1

    def test_get_lux_bucket_mid(self):
        """Test bucket assignment for mid-range lux."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            assert predictor._get_lux_bucket_index(5.0) == 4
            assert predictor._get_lux_bucket_index(50.0) == 7

    def test_get_lux_bucket_high(self):
        """Test bucket assignment for high lux."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            assert predictor._get_lux_bucket_index(500.0) == 10
            assert predictor._get_lux_bucket_index(5000.0) == 11


class TestBrightnessBuckets:
    """Tests for brightness bucketing functionality."""

    def test_get_brightness_bucket_dark(self):
        """Test bucket assignment for dark images."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            assert predictor._get_brightness_bucket_index(20) == 0
            assert predictor._get_brightness_bucket_index(50) == 1

    def test_get_brightness_bucket_ideal(self):
        """Test bucket assignment for ideal brightness."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            # 120 is the target brightness
            assert predictor._get_brightness_bucket_index(120) == 5

    def test_get_brightness_bucket_bright(self):
        """Test bucket assignment for bright images."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            assert predictor._get_brightness_bucket_index(200) == 9
            # 250 is in bucket 10 (220-255), bucket 11 is for 255+
            assert predictor._get_brightness_bucket_index(250) == 10


class TestPredictOptimalExposure:
    """Tests for exposure prediction."""

    def test_predict_no_data(self):
        """Test prediction with no learned data returns None."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            exposure, confidence = predictor.predict_optimal_exposure(50.0)

            assert exposure is None
            assert confidence == 0.0

    def test_predict_with_learned_data(self):
        """Test prediction with learned data returns value."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            # Simulate learned exposure for lux bucket 7 (50-100 lux)
            predictor.state["lux_exposure_map"]["7"] = [0.005, 100]

            exposure, confidence = predictor.predict_optimal_exposure(75.0)

            assert exposure == pytest.approx(0.005, rel=0.1)
            assert confidence == 1.0  # 100 samples = max confidence

    def test_predict_low_confidence(self):
        """Test prediction with few samples has low confidence."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            predictor.state["lux_exposure_map"]["5"] = [0.01, 10]

            exposure, confidence = predictor.predict_optimal_exposure(15.0)

            assert exposure is not None
            assert confidence == 0.1  # 10/100


class TestLearnFromFrame:
    """Tests for learning from frame metadata."""

    def test_learn_from_frame_updates_solar_pattern(self):
        """Test that learning updates solar patterns."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            metadata = {
                "ExposureTime": 5000,  # 0.005 seconds in microseconds
                "capture_timestamp": "2026-01-10T12:00:00",
                "diagnostics": {
                    "smoothed_lux": 100.0,
                    "brightness": {"mean_brightness": 120},
                },
            }

            predictor.learn_from_frame(metadata)

            # Check solar pattern was updated
            day_10 = str(10)  # January 10 = day 10
            assert day_10 in predictor.state["solar_patterns"]
            assert "12" in predictor.state["solar_patterns"][day_10]

    def test_learn_from_frame_updates_lux_exposure_map(self):
        """Test that learning updates lux-exposure map for good frames."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Frame with good brightness (120 - within 105-135 range)
            metadata = {
                "ExposureTime": 5000,  # 0.005 seconds
                "capture_timestamp": "2026-01-10T12:00:00",
                "diagnostics": {
                    "smoothed_lux": 100.0,
                    "brightness": {"mean_brightness": 120},
                },
            }

            predictor.learn_from_frame(metadata)

            # Should have learned this exposure
            bucket_idx = predictor._get_lux_bucket_index(100.0)
            bucket_key = str(bucket_idx)
            assert bucket_key in predictor.state["lux_exposure_map"]

    def test_learn_from_frame_skips_bad_brightness(self):
        """Test that learning skips frames with bad brightness."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Frame with bad brightness (too dark)
            metadata = {
                "ExposureTime": 5000,
                "capture_timestamp": "2026-01-10T12:00:00",
                "diagnostics": {
                    "smoothed_lux": 100.0,
                    "brightness": {"mean_brightness": 50},  # Too dark
                },
            }

            predictor.learn_from_frame(metadata)

            # Should NOT have learned this exposure
            bucket_idx = predictor._get_lux_bucket_index(100.0)
            bucket_key = str(bucket_idx)
            assert bucket_key not in predictor.state["lux_exposure_map"]

    def test_learn_from_frame_increments_predictions(self):
        """Test that learning increments prediction counters."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            initial_total = predictor.state["total_predictions"]
            initial_confidence = predictor.state["confidence"]

            metadata = {
                "ExposureTime": 5000,
                "capture_timestamp": "2026-01-10T12:00:00",
                "diagnostics": {
                    "smoothed_lux": 100.0,
                    "brightness": {"mean_brightness": 120},
                },
            }

            predictor.learn_from_frame(metadata)

            assert predictor.state["total_predictions"] == initial_total + 1
            # Good brightness (120) should increment confidence
            assert predictor.state["confidence"] == initial_confidence + 1


class TestTrustLevel:
    """Tests for trust level calculation."""

    def test_trust_starts_at_initial(self):
        """Test trust starts at initial_trust."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"initial_trust": 0.1}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)

            assert predictor.get_trust_level() == 0.1

    def test_trust_increases_with_confidence(self):
        """Test trust increases with good predictions."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"initial_trust": 0.0, "trust_increment": 0.001}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)
            predictor.state["confidence"] = 100

            assert predictor.get_trust_level() == 0.1  # 100 * 0.001

    def test_trust_capped_at_max(self):
        """Test trust is capped at max_trust."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"max_trust": 0.8, "trust_increment": 0.001}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)
            predictor.state["confidence"] = 10000  # Would be 10.0 without cap

            assert predictor.get_trust_level() == 0.8


class TestBlendWithFormula:
    """Tests for blending ML predictions with formula values."""

    def test_blend_none_ml_returns_formula(self):
        """Test that None ML value returns formula value."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            result = predictor.blend_with_formula(None, 0.01)
            assert result == 0.01

    def test_blend_shadow_mode_returns_formula(self):
        """Test that shadow mode returns formula value."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"shadow_mode": True}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)

            result = predictor.blend_with_formula(0.02, 0.01)
            assert result == 0.01

    def test_blend_zero_trust_returns_formula(self):
        """Test that zero trust returns formula value."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"initial_trust": 0.0}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)

            result = predictor.blend_with_formula(0.02, 0.01)
            assert result == 0.01

    def test_blend_full_trust_returns_ml(self):
        """Test that full trust returns ML value."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"initial_trust": 0.8, "max_trust": 0.8}
            predictor = MLExposurePredictor(config, state_dir=tmpdir)

            result = predictor.blend_with_formula(0.02, 0.01)
            # 0.8 * 0.02 + 0.2 * 0.01 = 0.016 + 0.002 = 0.018
            assert result == pytest.approx(0.018, rel=0.01)


class TestTransitionSpeed:
    """Tests for transition speed calculation."""

    def test_transition_speed_base(self):
        """Test base transition speed with no rapid changes."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            speed = predictor.get_transition_speed(120)  # Perfect brightness
            assert speed == pytest.approx(0.1, rel=0.1)

    def test_transition_speed_increases_with_brightness_error(self):
        """Test speed increases when brightness is far from target."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Large brightness error (60 vs target 120)
            speed = predictor.get_transition_speed(60)
            assert speed > 0.1  # Should be faster than base

    def test_transition_speed_with_rapid_lux_change(self):
        """Test speed increases with rapid lux changes."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Simulate rapid lux change
            now = time.time()
            predictor.lux_history.append((now - 60, 100))
            predictor.lux_history.append((now - 30, 50))
            predictor.lux_history.append((now, 10))  # Dropping fast

            speed = predictor.get_transition_speed(120)
            assert speed > 0.1  # Should be faster


class TestPredictFutureLux:
    """Tests for future lux prediction."""

    def test_predict_future_insufficient_history(self):
        """Test prediction returns None with insufficient history."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            result = predictor.predict_future_lux(3)
            assert result is None

    def test_predict_future_with_history(self):
        """Test prediction works with sufficient history."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Add history showing increasing lux
            now = time.time()
            for i in range(5):
                predictor.lux_history.append((now - (4 - i) * 30, 10 + i * 10))

            result = predictor.predict_future_lux(3)
            assert result is not None
            assert result > 50  # Should predict higher lux

    def test_predict_future_clamped(self):
        """Test prediction is clamped to valid range."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Add history showing rapidly decreasing lux
            now = time.time()
            for i in range(5):
                predictor.lux_history.append((now - (4 - i) * 30, 100 - i * 30))

            result = predictor.predict_future_lux(10)  # Predict far ahead
            assert result >= 0.01  # Should be clamped to min


class TestLinearRegression:
    """Tests for linear regression helper."""

    def test_regression_simple(self):
        """Test simple linear regression."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            x = [1, 2, 3, 4, 5]
            y = [2, 4, 6, 8, 10]  # y = 2x

            slope, intercept = predictor._linear_regression(x, y)

            assert slope == pytest.approx(2.0, rel=0.01)
            assert intercept == pytest.approx(0.0, rel=0.1)

    def test_regression_with_intercept(self):
        """Test linear regression with non-zero intercept."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            x = [0, 1, 2, 3, 4]
            y = [5, 7, 9, 11, 13]  # y = 2x + 5

            slope, intercept = predictor._linear_regression(x, y)

            assert slope == pytest.approx(2.0, rel=0.01)
            assert intercept == pytest.approx(5.0, rel=0.01)


class TestPersistence:
    """Tests for state persistence."""

    def test_save_and_load_state(self):
        """Test saving and loading state."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor1 = MLExposurePredictor({}, state_dir=tmpdir)
            predictor1.state["confidence"] = 999
            predictor1.state["lux_exposure_map"]["5"] = [0.01, 50]
            predictor1.save_state()

            # Create new predictor, should load saved state
            predictor2 = MLExposurePredictor({}, state_dir=tmpdir)

            assert predictor2.state["confidence"] == 999
            assert predictor2.state["lux_exposure_map"]["5"] == [0.01, 50]

    def test_load_nonexistent_state(self):
        """Test loading when no state file exists."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Should use default state
            assert predictor.state["confidence"] == 0
            assert predictor.state["total_predictions"] == 0


class TestGetStatistics:
    """Tests for statistics reporting."""

    def test_get_statistics(self):
        """Test statistics dictionary contents."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            predictor.state["confidence"] = 100
            predictor.state["total_predictions"] = 200

            stats = predictor.get_statistics()

            assert stats["confidence"] == 100
            assert stats["total_predictions"] == 200
            assert "trust_level" in stats
            assert "solar_pattern_days" in stats
            assert "shadow_mode" in stats


class TestGetExpectedLux:
    """Tests for expected lux lookup."""

    def test_get_expected_lux_no_data(self):
        """Test expected lux returns None when no pattern data."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            result = predictor.get_expected_lux()
            assert result is None

    def test_get_expected_lux_with_data(self):
        """Test expected lux returns value when pattern data exists."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Add pattern for current time
            from datetime import datetime

            now = datetime.now()
            day = str(now.timetuple().tm_yday)
            hour = str(now.hour)
            minute_bucket = str((now.minute // 15) * 15)

            predictor.state["solar_patterns"][day] = {hour: {minute_bucket: 75.0}}

            result = predictor.get_expected_lux()
            assert result == 75.0


class TestCorrectionFactor:
    """Tests for correction factor learning and retrieval."""

    def test_get_correction_factor_no_data(self):
        """Test correction factor returns None when no data."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            result = predictor.get_correction_factor(50.0, 100.0)
            assert result is None

    def test_get_correction_factor_with_data(self):
        """Test correction factor returns learned value."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            # Add correction memory
            lux_bucket = predictor._get_lux_bucket_index(50.0)
            brightness_bucket = predictor._get_brightness_bucket_index(100.0)
            key = f"{lux_bucket}_{brightness_bucket}"
            predictor.state["correction_memory"][key] = 1.15

            result = predictor.get_correction_factor(50.0, 100.0)
            assert result == 1.15


class TestLuxExposureTable:
    """Tests for lux-exposure table retrieval."""

    def test_get_lux_exposure_table_empty(self):
        """Test table is empty when no learned data."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)

            table = predictor.get_lux_exposure_table()
            assert table == []

    def test_get_lux_exposure_table_with_data(self):
        """Test table contains learned mappings."""
        from src.ml_exposure import MLExposurePredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = MLExposurePredictor({}, state_dir=tmpdir)
            predictor.state["lux_exposure_map"]["3"] = [0.5, 50]
            predictor.state["lux_exposure_map"]["7"] = [0.01, 100]

            table = predictor.get_lux_exposure_table()

            assert len(table) == 2
            assert any(entry["sample_count"] == 50 for entry in table)
            assert any(entry["sample_count"] == 100 for entry in table)
