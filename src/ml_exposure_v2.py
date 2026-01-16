"""
ML-based Adaptive Exposure Prediction System v2 - Database-Driven (Arctic-Aware)

This is an enhanced version that trains from the SQLite database instead of
learning frame-by-frame. Key improvements:

1. Train from historical "good" frames (brightness 100-140) AND aurora frames
2. Solar elevation-based predictions (season-agnostic, works at 68°N)
3. Use percentile data for early detection of clipping
4. Separate models for night/twilight/day conditions based on sun position

The system queries the database on initialization to build lookup tables,
then provides predictions based on proven working exposures.

Arctic-Aware Features:
- Uses sun elevation instead of clock hours (works year-round at any latitude)
- Includes high-contrast night frames (auroras) in training data
- Twilight is defined by sun position, not time of day
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

    Arctic-Aware: Uses sun elevation for time periods instead of clock hours,
    making it season-agnostic for high-latitude locations like Sortland (68°N).
    """

    # Lux bucket boundaries (logarithmic scale) - same as v1
    LUX_BUCKETS = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0]

    # Solar elevation-based periods (season-agnostic, works at any latitude)
    # These thresholds are based on astronomical definitions:
    # - Civil twilight: sun between 0° and -6°
    # - Nautical twilight: sun between -6° and -12°
    # - Astronomical twilight: sun between -12° and -18°
    SOLAR_PERIODS = {
        "night": (-90, -12),  # Deep night (astronomical night)
        "twilight": (-12, 0),  # Twilight (civil + nautical)
        "day": (0, 90),  # Daytime (sun above horizon)
    }

    # Fallback: Clock-based time periods (used when sun_elevation unavailable)
    TIME_PERIODS = {
        "night": list(range(0, 6)) + list(range(20, 24)),
        "twilight": list(range(6, 10)) + list(range(16, 20)),  # Renamed from transition
        "day": list(range(10, 16)),
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
        """Build lookup tables from historical database data (Arctic-aware)."""
        if not os.path.exists(self.db_path):
            logger.warning(f"[ML v2] Database not found at {self.db_path}")
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Query good frames - includes standard AND aurora/high-contrast night frames
            # Uses sun_elevation for solar-based periods (season-agnostic)
            cursor.execute(
                """
                SELECT
                    lux,
                    exposure_time_us,
                    brightness_mean,
                    brightness_p5,
                    brightness_p95,
                    strftime('%H', timestamp) as hour,
                    timestamp,
                    sun_elevation
                FROM captures
                WHERE (
                    -- Standard good frames (day/twilight)
                    (brightness_mean BETWEEN ? AND ?)
                    OR
                    -- High-contrast night frames (Aurora/Stars)
                    -- Dark overall but with bright highlights
                    (brightness_mean BETWEEN 30 AND 90
                     AND brightness_p95 > 150
                     AND lux < 5)
                )
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

            # Count frame types
            standard_count = sum(
                1
                for f in good_frames
                if self.good_brightness_min <= (f[2] or 0) <= self.good_brightness_max
            )
            aurora_count = len(good_frames) - standard_count

            logger.info(
                f"[ML v2] Training from {len(good_frames)} frames "
                f"(standard: {standard_count}, aurora: {aurora_count})"
            )

            # Build lux-exposure map with solar period awareness
            temp_map = {}  # (bucket, period) -> list of exposures

            for lux, exp_us, _bright, _p5, _p95, hour_str, _timestamp, sun_elev in good_frames:
                if lux is None or exp_us is None:
                    continue

                bucket = self._get_lux_bucket(lux)

                # Use solar period if sun_elevation available, else fall back to clock
                if sun_elev is not None:
                    period = self._get_solar_period(sun_elev)
                else:
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
            logger.exception(f"[ML v2] Training failed: {e}")

    def predict_optimal_exposure(
        self,
        lux: float,
        timestamp: Optional[float] = None,
        sun_elevation: Optional[float] = None,
    ) -> Tuple[Optional[float], float]:
        """
        Predict optimal exposure for given lux level and solar conditions.

        Arctic-Aware: Uses sun_elevation for period determination when available,
        making predictions season-agnostic. Falls back to clock-based periods
        if sun_elevation is not provided.

        NEW: If exact bucket match unavailable, interpolates between adjacent
        buckets to fill data gaps (e.g., 0.0-0.5 lux deep night, 5-20 lux transition).

        Args:
            lux: Current light level in lux
            timestamp: Unix timestamp (defaults to now, used for clock fallback)
            sun_elevation: Sun elevation in degrees (preferred for Arctic locations)

        Returns:
            Tuple of (predicted_exposure_seconds, confidence_factor)
            Returns (None, 0.0) if no prediction available
        """
        if timestamp is None:
            timestamp = time.time()

        bucket = self._get_lux_bucket(lux)

        # Use solar period if sun_elevation available, else fall back to clock
        if sun_elevation is not None:
            period = self._get_solar_period(sun_elevation)
        else:
            hour = datetime.fromtimestamp(timestamp).hour
            period = self._get_time_period(hour)

        # Try exact match first (bucket + period)
        key = f"{bucket}_{period}"
        if key in self.state["lux_exposure_map"]:
            exp_us, count = self.state["lux_exposure_map"][key]
            confidence = min(1.0, count / 100)  # More samples = more confidence
            exp_seconds = exp_us / 1_000_000
            logger.debug(
                f"[ML v2] Prediction: lux={lux:.1f}, bucket={bucket}, period={period} "
                f"(sun={sun_elevation}°) → {exp_seconds:.4f}s (conf={confidence:.2f}, samples={count})"
            )
            return exp_seconds, confidence

        # NEW: Try interpolation between adjacent buckets
        interpolated = self._interpolate_between_buckets(lux, period)
        if interpolated is not None:
            exp_seconds, confidence = interpolated
            logger.debug(
                f"[ML v2] Interpolated prediction: lux={lux:.1f}, bucket={bucket}, "
                f"period={period} → {exp_seconds:.4f}s (conf={confidence:.2f})"
            )
            return exp_seconds, confidence

        # Fall back to bucket-only match (any period)
        for fallback_period in ["day", "twilight", "night"]:
            fallback_key = f"{bucket}_{fallback_period}"
            if fallback_key in self.state["lux_exposure_map"]:
                exp_us, count = self.state["lux_exposure_map"][fallback_key]
                confidence = min(0.7, count / 100)  # Lower confidence for period mismatch
                exp_seconds = exp_us / 1_000_000
                logger.debug(
                    f"[ML v2] Fallback prediction: lux={lux:.1f}, bucket={bucket}, "
                    f"using {fallback_period} data → {exp_seconds:.4f}s (conf={confidence:.2f})"
                )
                return exp_seconds, confidence

        # Try interpolation across any period as last resort
        for fallback_period in ["day", "twilight", "night"]:
            if fallback_period == period:
                continue
            interpolated = self._interpolate_between_buckets(lux, fallback_period)
            if interpolated is not None:
                exp_seconds, confidence = interpolated
                # Reduce confidence for cross-period interpolation
                confidence *= 0.5
                logger.debug(
                    f"[ML v2] Cross-period interpolated: lux={lux:.1f}, "
                    f"using {fallback_period} → {exp_seconds:.4f}s (conf={confidence:.2f})"
                )
                return exp_seconds, confidence

        # No prediction available
        logger.debug(f"[ML v2] No prediction for lux={lux:.1f}, bucket={bucket}")
        return None, 0.0

    def _find_adjacent_buckets(
        self, lux: float, period: str
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Find the nearest lower and upper buckets that have data for this period.

        Args:
            lux: Current light level in lux
            period: Time period ("night", "twilight", or "day")

        Returns:
            Tuple of (lower_bucket_index, upper_bucket_index)
            Either may be None if no adjacent bucket has data
        """
        current_bucket = self._get_lux_bucket(lux)

        lower_bucket = None
        upper_bucket = None

        # Search downward for lower bucket with data
        for b in range(current_bucket - 1, -1, -1):
            key = f"{b}_{period}"
            if key in self.state["lux_exposure_map"]:
                lower_bucket = b
                break

        # Search upward for upper bucket with data
        for b in range(current_bucket + 1, len(self.LUX_BUCKETS)):
            key = f"{b}_{period}"
            if key in self.state["lux_exposure_map"]:
                upper_bucket = b
                break

        return lower_bucket, upper_bucket

    def _interpolate_between_buckets(
        self, lux: float, period: str
    ) -> Optional[Tuple[float, float]]:
        """
        Interpolate exposure prediction between adjacent buckets.

        Uses logarithmic interpolation in both lux and exposure space
        since both follow logarithmic relationships.

        Args:
            lux: Current light level in lux
            period: Time period ("night", "twilight", or "day")

        Returns:
            Tuple of (exposure_seconds, confidence) or None if interpolation not possible
        """
        lower_bucket, upper_bucket = self._find_adjacent_buckets(lux, period)

        # Get lux threshold for bucket boundary
        def bucket_lux(bucket_idx: int) -> float:
            if bucket_idx < 0:
                return 0.01
            if bucket_idx >= len(self.LUX_BUCKETS):
                return self.LUX_BUCKETS[-1] * 2
            return self.LUX_BUCKETS[bucket_idx]

        # Case 1: Both adjacent buckets have data - interpolate between them
        if lower_bucket is not None and upper_bucket is not None:
            lower_key = f"{lower_bucket}_{period}"
            upper_key = f"{upper_bucket}_{period}"

            lower_exp_us, lower_count = self.state["lux_exposure_map"][lower_key]
            upper_exp_us, upper_count = self.state["lux_exposure_map"][upper_key]

            # Get representative lux values for each bucket
            lower_lux = bucket_lux(lower_bucket)
            upper_lux = bucket_lux(upper_bucket)

            # Interpolate in log space
            if lower_lux > 0 and upper_lux > 0 and lower_exp_us > 0 and upper_exp_us > 0:
                log_lux = math.log10(max(0.01, lux))
                log_lower = math.log10(lower_lux)
                log_upper = math.log10(upper_lux)

                # Calculate interpolation factor (0 = lower, 1 = upper)
                if log_upper != log_lower:
                    t = (log_lux - log_lower) / (log_upper - log_lower)
                    t = max(0.0, min(1.0, t))
                else:
                    t = 0.5

                # Interpolate exposure in log space
                log_exp_lower = math.log10(lower_exp_us)
                log_exp_upper = math.log10(upper_exp_us)
                log_exp = log_exp_lower + t * (log_exp_upper - log_exp_lower)
                exp_us = 10**log_exp

                exp_seconds = exp_us / 1_000_000

                # Confidence based on both buckets, reduced for interpolation
                base_conf = min(lower_count, upper_count) / 100
                confidence = min(0.8, base_conf) * 0.7  # 70% of base for interpolation

                return exp_seconds, confidence

        # Case 2: Only one adjacent bucket has data - use nearest with reduced confidence
        nearest_bucket = lower_bucket if lower_bucket is not None else upper_bucket
        if nearest_bucket is not None:
            key = f"{nearest_bucket}_{period}"
            exp_us, count = self.state["lux_exposure_map"][key]
            exp_seconds = exp_us / 1_000_000
            # 50% confidence for nearest-only extrapolation
            confidence = min(0.5, count / 100) * 0.5

            logger.debug(
                f"[ML v2] Nearest bucket extrapolation: bucket={nearest_bucket}, "
                f"exp={exp_seconds:.4f}s, conf={confidence:.2f}"
            )
            return exp_seconds, confidence

        return None

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
        """Get time period name for an hour (fallback when sun_elevation unavailable)."""
        for period, hours in self.TIME_PERIODS.items():
            if hour in hours:
                return period
        return "day"  # Default

    def _get_solar_period(self, sun_elevation: float) -> str:
        """
        Get time period based on sun elevation (Arctic-aware, season-agnostic).

        This is the preferred method for determining time periods as it works
        correctly year-round at any latitude, including polar regions where
        clock-based methods fail during polar day/night.

        Args:
            sun_elevation: Sun elevation in degrees (-90 to +90)
                - Negative = below horizon
                - Positive = above horizon

        Returns:
            Period name: "night", "twilight", or "day"
        """
        for period, (min_elev, max_elev) in self.SOLAR_PERIODS.items():
            if min_elev <= sun_elevation < max_elev:
                return period

        # Default based on sign
        return "day" if sun_elevation >= 0 else "night"

    def _save_state(self):
        """Save current state to disk."""
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"[ML v2] State saved to {self.state_file}")
        except Exception as e:
            logger.exception(f"[ML v2] Failed to save state: {e}")

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
