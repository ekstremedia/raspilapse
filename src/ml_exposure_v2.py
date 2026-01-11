"""
ML-based Adaptive Exposure Prediction System v2 - Database-Driven

This is an enhanced version that trains from the SQLite database instead of
learning frame-by-frame. Key improvements:

1. Train from historical "good" frames (brightness 100-140 only)
2. Time-of-day aware exposure predictions
3. Use percentile data for early detection of clipping
4. Separate models for night/transition/day conditions

The system queries the database on initialization to build lookup tables,
then provides predictions based on proven working exposures.
"""

import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MLExposurePredictorV2:
    """
    Database-driven ML system for adaptive exposure prediction.

    Unlike v1 which learns incrementally, v2 builds its model from
    historical database data - ensuring it only learns from frames
    with good brightness (avoiding reinforcing bad exposures).
    """

    # Lux bucket boundaries (logarithmic scale) - same as v1
    LUX_BUCKETS = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0]

    # Time periods for time-aware predictions
    TIME_PERIODS = {
        "night": list(range(0, 6)) + list(range(20, 24)),  # 00:00-05:59, 20:00-23:59
        "morning_transition": list(range(6, 10)),  # 06:00-09:59
        "day": list(range(10, 14)),  # 10:00-13:59
        "evening_transition": list(range(14, 20)),  # 14:00-19:59
    }

    def __init__(self, db_path: str, config: Dict, state_dir: str = "ml_state"):
        """
        Initialize the ML v2 predictor.

        Args:
            db_path: Path to SQLite database
            config: ML configuration from config.yml
            state_dir: Directory to store persistent state
        """
        self.db_path = db_path
        self.config = config
        self.state_dir = state_dir
        self.state_file = os.path.join(state_dir, config.get("state_file_v2", "ml_state_v2.json"))

        # Brightness range for "good" frames
        self.good_brightness_min = config.get("good_brightness_min", 100)
        self.good_brightness_max = config.get("good_brightness_max", 140)

        # Minimum samples required per bucket
        self.min_samples = config.get("min_samples", 10)

        # Trust level (v2 starts with higher trust since it's trained on good data)
        self.initial_trust = config.get("initial_trust_v2", 0.5)
        self.max_trust = config.get("max_trust", 0.8)

        # State
        self.state = {
            "lux_exposure_map": {},  # (lux_bucket, time_period) -> (exposure_us, count)
            "percentile_thresholds": {},  # Learned thresholds for early detection
            "training_stats": {},  # Statistics about training data
            "last_trained": None,
            "version": 2,
        }

        # Load existing state or train from database
        self._initialize()

        logger.info(
            f"[ML v2] Initialized with trust={self.initial_trust:.2f}, "
            f"buckets={len(self.state['lux_exposure_map'])}"
        )

    def _initialize(self):
        """Initialize by loading state or training from database."""
        if os.path.exists(self.state_file):
            self._load_state()
            # Check if we should retrain (e.g., state is stale)
            last_trained = self.state.get("last_trained")
            if last_trained:
                try:
                    trained_time = datetime.fromisoformat(last_trained)
                    hours_since = (datetime.now() - trained_time).total_seconds() / 3600
                    if hours_since > 24:  # Retrain daily
                        logger.info(f"[ML v2] State is {hours_since:.1f}h old, retraining...")
                        self._train_from_database()
                except (ValueError, TypeError):
                    pass
        else:
            # No existing state - train from database
            self._train_from_database()

    def _train_from_database(self):
        """Build lookup tables from historical database data."""
        if not os.path.exists(self.db_path):
            logger.warning(f"[ML v2] Database not found at {self.db_path}")
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Query good frames (brightness within target range)
            cursor.execute(
                """
                SELECT
                    lux,
                    exposure_time_us,
                    brightness_mean,
                    brightness_p5,
                    brightness_p95,
                    strftime('%H', timestamp) as hour,
                    timestamp
                FROM captures
                WHERE brightness_mean BETWEEN ? AND ?
                AND exposure_time_us > 0
                AND lux > 0
                ORDER BY timestamp DESC
                LIMIT 10000
            """,
                (self.good_brightness_min, self.good_brightness_max),
            )

            good_frames = cursor.fetchall()
            conn.close()

            if not good_frames:
                logger.warning("[ML v2] No good frames found in database")
                return

            logger.info(f"[ML v2] Training from {len(good_frames)} good frames")

            # Build lux-exposure map with time awareness
            temp_map = {}  # (bucket, period) -> list of exposures

            for lux, exp_us, bright, p5, p95, hour_str, timestamp in good_frames:
                if lux is None or exp_us is None:
                    continue

                bucket = self._get_lux_bucket(lux)
                period = self._get_time_period(int(hour_str) if hour_str else 12)
                key = f"{bucket}_{period}"

                if key not in temp_map:
                    temp_map[key] = []
                temp_map[key].append(exp_us)

            # Average each bucket and store
            self.state["lux_exposure_map"] = {}
            for key, exposures in temp_map.items():
                if len(exposures) >= self.min_samples:
                    avg_exp = sum(exposures) / len(exposures)
                    self.state["lux_exposure_map"][key] = [avg_exp, len(exposures)]

            # Store training statistics
            self.state["training_stats"] = {
                "total_good_frames": len(good_frames),
                "buckets_trained": len(self.state["lux_exposure_map"]),
                "brightness_range": [self.good_brightness_min, self.good_brightness_max],
            }
            self.state["last_trained"] = datetime.now().isoformat()

            # Save state
            self._save_state()

            logger.info(
                f"[ML v2] Training complete: {len(self.state['lux_exposure_map'])} buckets "
                f"from {len(good_frames)} good frames"
            )

        except Exception as e:
            logger.error(f"[ML v2] Training failed: {e}")

    def predict_optimal_exposure(
        self, lux: float, timestamp: Optional[float] = None
    ) -> Tuple[Optional[float], float]:
        """
        Predict optimal exposure for given lux level and time.

        Args:
            lux: Current light level in lux
            timestamp: Unix timestamp (defaults to now)

        Returns:
            Tuple of (predicted_exposure_seconds, confidence_factor)
            Returns (None, 0.0) if no prediction available
        """
        if timestamp is None:
            timestamp = time.time()

        hour = datetime.fromtimestamp(timestamp).hour
        bucket = self._get_lux_bucket(lux)
        period = self._get_time_period(hour)

        # Try exact match first (bucket + period)
        key = f"{bucket}_{period}"
        if key in self.state["lux_exposure_map"]:
            exp_us, count = self.state["lux_exposure_map"][key]
            confidence = min(1.0, count / 100)  # More samples = more confidence
            exp_seconds = exp_us / 1_000_000
            logger.debug(
                f"[ML v2] Prediction: lux={lux:.1f}, bucket={bucket}, period={period} "
                f"→ {exp_seconds:.4f}s (conf={confidence:.2f}, samples={count})"
            )
            return exp_seconds, confidence

        # Fall back to bucket-only match (any time period)
        for fallback_period in ["day", "morning_transition", "evening_transition", "night"]:
            fallback_key = f"{bucket}_{fallback_period}"
            if fallback_key in self.state["lux_exposure_map"]:
                exp_us, count = self.state["lux_exposure_map"][fallback_key]
                confidence = min(0.7, count / 100)  # Lower confidence for time mismatch
                exp_seconds = exp_us / 1_000_000
                logger.debug(
                    f"[ML v2] Fallback prediction: lux={lux:.1f}, bucket={bucket}, "
                    f"using {fallback_period} data → {exp_seconds:.4f}s (conf={confidence:.2f})"
                )
                return exp_seconds, confidence

        # No prediction available
        logger.debug(f"[ML v2] No prediction for lux={lux:.1f}, bucket={bucket}")
        return None, 0.0

    def predict_optimal_gain(self, lux: float) -> Optional[float]:
        """Predict optimal gain (not implemented in v2 yet)."""
        return None

    def get_trust_level(self) -> float:
        """
        Get current trust level for ML predictions.

        V2 starts with higher trust since it's trained on good data.
        """
        bucket_count = len(self.state.get("lux_exposure_map", {}))
        # More buckets = more trust, up to max
        trust = self.initial_trust + (bucket_count * 0.02)
        return min(self.max_trust, trust)

    def blend_with_formula(self, ml_value: Optional[float], formula_value: float) -> float:
        """
        Blend ML prediction with formula-based value.

        Args:
            ml_value: ML predicted value (or None)
            formula_value: Formula-based fallback value

        Returns:
            Blended value based on current trust level
        """
        if ml_value is None:
            return formula_value

        trust = self.get_trust_level()
        blended = trust * ml_value + (1 - trust) * formula_value

        logger.debug(
            f"[ML v2] Blend: ML={ml_value:.3f}, formula={formula_value:.3f}, "
            f"trust={trust:.2f}, result={blended:.3f}"
        )

        return blended

    def get_statistics(self) -> Dict:
        """Get current ML statistics for debugging."""
        return {
            "version": 2,
            "trust_level": self.get_trust_level(),
            "lux_exposure_buckets": len(self.state.get("lux_exposure_map", {})),
            "training_stats": self.state.get("training_stats", {}),
            "last_trained": self.state.get("last_trained"),
        }

    def _get_lux_bucket(self, lux: float) -> int:
        """Get bucket index for a lux value."""
        for i, threshold in enumerate(self.LUX_BUCKETS[1:], 1):
            if lux < threshold:
                return i - 1
        return len(self.LUX_BUCKETS) - 1

    def _get_time_period(self, hour: int) -> str:
        """Get time period name for an hour."""
        for period, hours in self.TIME_PERIODS.items():
            if hour in hours:
                return period
        return "day"  # Default

    def _save_state(self):
        """Save current state to disk."""
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"[ML v2] State saved to {self.state_file}")
        except Exception as e:
            logger.error(f"[ML v2] Failed to save state: {e}")

    def _load_state(self):
        """Load state from disk if available."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    loaded = json.load(f)
                    self.state.update(loaded)
                logger.info(
                    f"[ML v2] Loaded state: {len(self.state.get('lux_exposure_map', {}))} buckets, "
                    f"trained: {self.state.get('last_trained', 'unknown')}"
                )
            except Exception as e:
                logger.warning(f"[ML v2] Failed to load state: {e}")

    def retrain(self):
        """Force retraining from database."""
        logger.info("[ML v2] Forcing retrain from database...")
        self._train_from_database()
