"""
ML-based Adaptive Exposure Prediction System

A lightweight machine learning system that continuously learns and improves
timelapse exposure settings. Designed to run on Raspberry Pi with minimal
compute requirements.

Components:
1. Solar Pattern Memory - Learn expected lux for each time/day
2. Lux-Exposure Mapper - Learn optimal exposure for each lux level
3. Trend Predictor - Anticipate light changes
4. Correction Memory - Remember what brightness corrections worked
5. Transition Speed Optimizer - Dynamically adjust interpolation speeds
"""

import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MLExposurePredictor:
    """
    Lightweight ML system for adaptive exposure prediction.

    Uses simple statistical learning techniques suitable for Raspberry Pi:
    - Lookup tables with exponential moving average updates
    - Linear trend extrapolation for prediction
    - Bucketized storage for memory efficiency
    """

    # Lux bucket boundaries (logarithmic scale)
    LUX_BUCKETS = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0]

    # Brightness bucket boundaries
    BRIGHTNESS_BUCKETS = [0, 40, 60, 80, 100, 120, 140, 160, 180, 200, 220, 255]

    def __init__(self, config: Dict, state_dir: str = "ml_state"):
        """
        Initialize the ML predictor.

        Args:
            config: ML configuration from config.yml
            state_dir: Directory to store persistent state
        """
        self.config = config
        self.state_dir = state_dir
        self.state_file = os.path.join(state_dir, config.get("state_file", "ml_state.json"))

        # Learning rates
        self.solar_learning_rate = config.get("solar_learning_rate", 0.1)
        self.exposure_learning_rate = config.get("exposure_learning_rate", 0.05)
        self.correction_learning_rate = config.get("correction_learning_rate", 0.1)

        # Trust building
        self.initial_trust = config.get("initial_trust", 0.0)
        self.trust_increment = config.get("trust_increment", 0.001)
        self.max_trust = config.get("max_trust", 0.8)

        # Prediction settings
        self.trend_window = config.get("trend_window", 10)
        self.prediction_frames = config.get("prediction_frames", 3)

        # Shadow mode (log predictions but don't use them)
        self.shadow_mode = config.get("shadow_mode", False)

        # Runtime state (not persisted)
        self.lux_history: deque = deque(maxlen=self.trend_window)
        self.last_brightness: Optional[float] = None
        self.last_correction: Optional[float] = None
        self.last_lux: Optional[float] = None

        # Persisted state
        self.state = {
            "solar_patterns": {},  # day_of_year -> hour -> minute_bucket -> lux
            "lux_exposure_map": {},  # lux_bucket_idx -> (exposure, count)
            "correction_memory": {},  # (lux_bucket, brightness_bucket) -> correction
            "confidence": 0,  # Total good predictions
            "total_predictions": 0,
            "version": 1,
        }

        # Load existing state
        self.load_state()

        logger.info(
            f"[ML] Initialized with trust={self.initial_trust:.2f}, "
            f"confidence={self.state['confidence']}, shadow_mode={self.shadow_mode}"
        )

    # =========================================================================
    # Core Prediction Methods
    # =========================================================================

    def predict_optimal_exposure(
        self, lux: float, timestamp: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Predict optimal exposure for given lux level.

        Args:
            lux: Current light level in lux
            timestamp: Unix timestamp (defaults to now)

        Returns:
            Tuple of (predicted_exposure_seconds, confidence_factor)
        """
        if timestamp is None:
            timestamp = time.time()

        # Get learned exposure from lux-exposure map
        bucket_idx = self._get_lux_bucket_index(lux)
        bucket_key = str(bucket_idx)

        if bucket_key in self.state["lux_exposure_map"]:
            learned_exp, count = self.state["lux_exposure_map"][bucket_key]
            confidence = min(1.0, count / 100)  # More samples = more confidence
        else:
            # No learned data - return None to fall back to formula
            return None, 0.0

        # Check if we should adjust based on predicted future lux
        predicted_lux = self.predict_future_lux(self.prediction_frames)
        if predicted_lux is not None and abs(predicted_lux - lux) > lux * 0.2:
            # Lux changing significantly - adjust exposure proactively
            lux_ratio = lux / max(0.01, predicted_lux)
            # If lux will drop (ratio > 1), increase exposure
            # If lux will rise (ratio < 1), decrease exposure
            adjustment = math.sqrt(lux_ratio)  # Gentle adjustment
            learned_exp *= adjustment
            logger.debug(
                f"[ML] Proactive adjustment: lux {lux:.1f} -> {predicted_lux:.1f}, "
                f"exposure *= {adjustment:.2f}"
            )

        return learned_exp, confidence

    def predict_optimal_gain(self, lux: float) -> Optional[float]:
        """
        Predict optimal gain for given lux level.

        Currently returns None to defer to existing formula.
        Future: Could learn optimal gain settings.
        """
        # TODO: Implement gain learning if needed
        return None

    def get_transition_speed(
        self, current_brightness: float, target_brightness: float = 120
    ) -> Optional[float]:
        """
        Get optimal transition speed based on conditions.

        Args:
            current_brightness: Current image brightness (0-255)
            target_brightness: Target brightness (default 120)

        Returns:
            Recommended transition speed, or None to use default
        """
        # Calculate lux change rate from history
        lux_change_rate = self._calculate_lux_change_rate()

        # Base speed
        speed = 0.10

        # If lux changing rapidly, speed up
        if lux_change_rate is not None and abs(lux_change_rate) > 0.1:
            # lux_change_rate is in lux/second
            # At 0.5 lux/sec change, double the speed
            speed_multiplier = 1.0 + min(4.0, abs(lux_change_rate) * 2)
            speed = min(0.5, speed * speed_multiplier)

        # If brightness way off target, speed up
        brightness_error = abs(current_brightness - target_brightness)
        if brightness_error > 50:
            speed = min(0.7, speed * 1.5)
        elif brightness_error > 30:
            speed = min(0.5, speed * 1.2)

        return speed

    def get_correction_factor(self, lux: float, brightness: float) -> Optional[float]:
        """
        Get learned correction factor for current conditions.

        Args:
            lux: Current light level
            brightness: Current image brightness

        Returns:
            Correction factor to apply, or None if no learned data
        """
        lux_bucket = self._get_lux_bucket_index(lux)
        brightness_bucket = self._get_brightness_bucket_index(brightness)
        key = f"{lux_bucket}_{brightness_bucket}"

        if key in self.state["correction_memory"]:
            return self.state["correction_memory"][key]

        return None

    def get_expected_lux(self, timestamp: Optional[float] = None) -> Optional[float]:
        """
        Get expected lux for the given time based on solar patterns.

        Args:
            timestamp: Unix timestamp (defaults to now)

        Returns:
            Expected lux, or None if no pattern data
        """
        if timestamp is None:
            timestamp = time.time()

        dt = datetime.fromtimestamp(timestamp)
        day_of_year = dt.timetuple().tm_yday
        hour = dt.hour
        minute_bucket = (dt.minute // 15) * 15  # 15-minute buckets

        day_key = str(day_of_year)
        hour_key = str(hour)
        minute_key = str(minute_bucket)

        try:
            return self.state["solar_patterns"][day_key][hour_key][minute_key]
        except KeyError:
            return None

    def predict_future_lux(self, frames_ahead: int = 3) -> Optional[float]:
        """
        Predict lux N frames ahead using linear extrapolation.

        Args:
            frames_ahead: Number of frames to predict ahead

        Returns:
            Predicted lux, or None if insufficient history
        """
        if len(self.lux_history) < 3:
            return None

        # Get recent readings
        recent = list(self.lux_history)[-5:]
        if len(recent) < 2:
            return None

        times = [t for t, l in recent]
        luxes = [l for t, l in recent]

        # Simple linear regression (no numpy needed)
        slope, intercept = self._linear_regression(times, luxes)

        # Predict future lux
        # Assume 30 second interval (could be configurable)
        interval = 30
        if len(times) >= 2:
            interval = (times[-1] - times[0]) / (len(times) - 1)

        future_time = times[-1] + (frames_ahead * interval)
        predicted_lux = slope * future_time + intercept

        # Clamp to valid range
        return max(0.01, min(10000, predicted_lux))

    # =========================================================================
    # Learning Methods
    # =========================================================================

    def learn_from_frame(self, metadata: Dict) -> None:
        """
        Learn from a captured frame's metadata.

        This is called after each frame capture to update the ML state.

        Args:
            metadata: Frame metadata dictionary
        """
        # Extract relevant data
        diagnostics = metadata.get("diagnostics", {})
        brightness_info = diagnostics.get("brightness", {})

        lux = diagnostics.get("smoothed_lux") or diagnostics.get("raw_lux")
        exposure = metadata.get("ExposureTime", 0) / 1_000_000  # Convert to seconds
        brightness = brightness_info.get("mean_brightness")
        timestamp = metadata.get("capture_timestamp")

        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp)
                unix_timestamp = dt.timestamp()
            except (ValueError, TypeError):
                unix_timestamp = time.time()
        else:
            unix_timestamp = time.time()

        # Lux is required for any learning
        if lux is None:
            logger.debug("[ML] Skipping learn - missing lux")
            return

        # Update lux history for trend prediction
        self.lux_history.append((unix_timestamp, lux))

        # Update solar patterns (always, as long as we have lux)
        self._update_solar_pattern(unix_timestamp, lux)

        # Increment total predictions
        self.state["total_predictions"] += 1

        # The following require brightness data
        if brightness is not None:
            # Update lux-exposure map if brightness was good
            self._update_lux_exposure_map(lux, exposure, brightness)

            # Update correction memory
            if self.last_lux is not None and self.last_correction is not None:
                self._update_correction_memory(
                    self.last_lux, self.last_brightness, self.last_correction, brightness
                )

            # Store for next iteration
            self.last_brightness = brightness
            self.last_correction = diagnostics.get("brightness_correction_factor", 1.0)

            # Check if prediction was good (for confidence building)
            if 100 <= brightness <= 140:  # Within good range
                self.state["confidence"] += 1

        # Always store last lux
        self.last_lux = lux

        # Periodically save state (every 100 frames)
        if self.state["total_predictions"] % 100 == 0:
            self.save_state()

    def _update_solar_pattern(self, timestamp: float, lux: float) -> None:
        """Update solar pattern memory for this time of day."""
        dt = datetime.fromtimestamp(timestamp)
        day_of_year = dt.timetuple().tm_yday
        hour = dt.hour
        minute_bucket = (dt.minute // 15) * 15

        day_key = str(day_of_year)
        hour_key = str(hour)
        minute_key = str(minute_bucket)

        # Initialize nested dicts if needed
        if day_key not in self.state["solar_patterns"]:
            self.state["solar_patterns"][day_key] = {}
        if hour_key not in self.state["solar_patterns"][day_key]:
            self.state["solar_patterns"][day_key][hour_key] = {}

        # Exponential moving average update
        if minute_key in self.state["solar_patterns"][day_key][hour_key]:
            old_value = self.state["solar_patterns"][day_key][hour_key][minute_key]
            new_value = self.solar_learning_rate * lux + (1 - self.solar_learning_rate) * old_value
        else:
            new_value = lux

        self.state["solar_patterns"][day_key][hour_key][minute_key] = new_value

    def _update_lux_exposure_map(self, lux: float, exposure: float, brightness: float) -> None:
        """Update lux-to-exposure mapping if brightness was good."""
        # Only learn from frames with good brightness (near target)
        if not (105 <= brightness <= 135):
            return

        bucket_idx = self._get_lux_bucket_index(lux)
        bucket_key = str(bucket_idx)

        if bucket_key in self.state["lux_exposure_map"]:
            old_exp, count = self.state["lux_exposure_map"][bucket_key]
            # Weighted update - newer data weighted more heavily initially
            weight = min(count, 100) / (count + 1)
            new_exp = weight * old_exp + (1 - weight) * exposure
            self.state["lux_exposure_map"][bucket_key] = [new_exp, count + 1]
        else:
            self.state["lux_exposure_map"][bucket_key] = [exposure, 1]

        logger.debug(
            f"[ML] Updated lux-exposure map: bucket {bucket_idx} "
            f"(lux ~{lux:.1f}) -> exposure {exposure:.3f}s"
        )

    def _update_correction_memory(
        self,
        prev_lux: float,
        prev_brightness: float,
        correction_applied: float,
        result_brightness: float,
    ) -> None:
        """Update correction memory based on what worked."""
        if prev_brightness is None:
            return

        lux_bucket = self._get_lux_bucket_index(prev_lux)
        brightness_bucket = self._get_brightness_bucket_index(prev_brightness)
        key = f"{lux_bucket}_{brightness_bucket}"

        # Did the correction help?
        prev_error = abs(prev_brightness - 120)
        new_error = abs(result_brightness - 120)

        if new_error < prev_error:
            # Correction helped - remember it
            if key in self.state["correction_memory"]:
                old_correction = self.state["correction_memory"][key]
                new_correction = (
                    self.correction_learning_rate * correction_applied
                    + (1 - self.correction_learning_rate) * old_correction
                )
            else:
                new_correction = correction_applied

            self.state["correction_memory"][key] = new_correction
            logger.debug(
                f"[ML] Learned correction: lux_bucket={lux_bucket}, "
                f"brightness_bucket={brightness_bucket} -> {new_correction:.3f}"
            )

    # =========================================================================
    # Trust and Blending
    # =========================================================================

    def get_trust_level(self) -> float:
        """
        Get current trust level for ML predictions.

        Trust increases with successful predictions, up to max_trust.

        Returns:
            Trust level between 0.0 and max_trust
        """
        base_trust = self.initial_trust
        earned_trust = self.state["confidence"] * self.trust_increment
        return min(self.max_trust, base_trust + earned_trust)

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

        if self.shadow_mode:
            # In shadow mode, log ML prediction but return formula value
            logger.info(f"[ML Shadow] Predicted {ml_value:.3f}, using formula {formula_value:.3f}")
            return formula_value

        trust = self.get_trust_level()
        blended = trust * ml_value + (1 - trust) * formula_value

        logger.debug(
            f"[ML] Blend: ML={ml_value:.3f}, formula={formula_value:.3f}, "
            f"trust={trust:.2f}, result={blended:.3f}"
        )

        return blended

    # =========================================================================
    # Persistence
    # =========================================================================

    def save_state(self) -> None:
        """Save current state to disk."""
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"[ML] State saved to {self.state_file}")
        except Exception as e:
            logger.error(f"[ML] Failed to save state: {e}")

    def load_state(self) -> None:
        """Load state from disk if available."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    loaded = json.load(f)
                    # Merge with defaults (in case new fields added)
                    self.state.update(loaded)
                logger.info(
                    f"[ML] Loaded state: {self.state['confidence']} good predictions, "
                    f"{self.state['total_predictions']} total"
                )
            except Exception as e:
                logger.warning(f"[ML] Failed to load state: {e}")

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_lux_bucket_index(self, lux: float) -> int:
        """Get bucket index for a lux value."""
        for i, threshold in enumerate(self.LUX_BUCKETS[1:], 1):
            if lux < threshold:
                return i - 1
        return len(self.LUX_BUCKETS) - 1

    def _get_brightness_bucket_index(self, brightness: float) -> int:
        """Get bucket index for a brightness value."""
        for i, threshold in enumerate(self.BRIGHTNESS_BUCKETS[1:], 1):
            if brightness < threshold:
                return i - 1
        return len(self.BRIGHTNESS_BUCKETS) - 1

    def _calculate_lux_change_rate(self) -> Optional[float]:
        """Calculate rate of lux change (lux per second)."""
        if len(self.lux_history) < 2:
            return None

        recent = list(self.lux_history)[-5:]
        if len(recent) < 2:
            return None

        times = [t for t, l in recent]
        luxes = [l for t, l in recent]

        # Time span
        time_span = times[-1] - times[0]
        if time_span <= 0:
            return None

        # Lux change
        lux_change = luxes[-1] - luxes[0]

        return lux_change / time_span

    def _linear_regression(self, x: List[float], y: List[float]) -> Tuple[float, float]:
        """
        Simple linear regression without numpy.

        Returns:
            Tuple of (slope, intercept)
        """
        n = len(x)
        if n < 2:
            return 0.0, y[0] if y else 0.0

        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_xx = sum(xi * xi for xi in x)

        denominator = n * sum_xx - sum_x * sum_x
        if abs(denominator) < 1e-10:
            return 0.0, sum_y / n

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n

        return slope, intercept

    # =========================================================================
    # Statistics and Debugging
    # =========================================================================

    def get_statistics(self) -> Dict:
        """Get current ML statistics for debugging."""
        return {
            "confidence": self.state["confidence"],
            "total_predictions": self.state["total_predictions"],
            "trust_level": self.get_trust_level(),
            "solar_pattern_days": len(self.state["solar_patterns"]),
            "lux_exposure_buckets": len(self.state["lux_exposure_map"]),
            "correction_memory_entries": len(self.state["correction_memory"]),
            "lux_history_length": len(self.lux_history),
            "shadow_mode": self.shadow_mode,
        }

    def get_lux_exposure_table(self) -> List[Dict]:
        """Get the learned lux-exposure mapping as a readable table."""
        table = []
        for bucket_key, (exposure, count) in self.state["lux_exposure_map"].items():
            bucket_idx = int(bucket_key)
            if bucket_idx < len(self.LUX_BUCKETS) - 1:
                lux_min = self.LUX_BUCKETS[bucket_idx]
                lux_max = self.LUX_BUCKETS[bucket_idx + 1]
            else:
                lux_min = self.LUX_BUCKETS[-1]
                lux_max = float("inf")

            table.append(
                {
                    "lux_range": f"{lux_min}-{lux_max}",
                    "exposure_s": exposure,
                    "sample_count": count,
                }
            )

        return sorted(table, key=lambda x: float(x["lux_range"].split("-")[0]))
