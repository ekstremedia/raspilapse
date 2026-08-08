"""`raspilapse-drtest` -- capture the same scene with every dynamic-range
method, side by side.

The trial tool: one metering pass with auto-exposure picks a base exposure,
then each method captures the identical scene with identical settings into
one directory, labelled by method. Compare the files, read the timing table,
pick your method. Also the measurement harness for the numbers the daemon's
budget guards assume (bracket settle frames, fusion time, develop time).

The daemon owns the camera; stop it first:

    sudo systemctl stop raspilapse
    raspilapse-drtest
    sudo systemctl start raspilapse
"""

import argparse
import copy
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from raspilapse.camera.capture import CameraConfig, ImageCapture
from raspilapse.dynrange import DynamicRange
from raspilapse.logging_setup import get_logger

logger = get_logger("drtest")

# Every trialable method spec: (label, method, tone_map enabled).
METHOD_SPECS = {
    "off": ("off", False),
    "tone_map": ("tone_map", False),
    "fusion": ("fusion", False),
    "fusion+tm": ("fusion", True),
    "sensor_hdr": ("sensor_hdr", False),
    "raw": ("raw", False),
}

DEFAULT_METHODS = "off,tone_map,fusion,sensor_hdr,raw"

# How long the metering pass lets auto-exposure converge before trusting it.
_METERING_FRAMES = 12


def parse_methods(spec: str) -> List[str]:
    """Validate a comma-separated method list, preserving order.

    "off" is always included, first -- every comparison needs its reference
    frame -- and duplicates collapse.
    """
    requested = [token.strip() for token in spec.split(",") if token.strip()]
    unknown = [token for token in requested if token not in METHOD_SPECS]
    if unknown:
        raise ValueError(
            f"Unknown method(s): {', '.join(unknown)} (choose from {', '.join(METHOD_SPECS)})"
        )
    ordered = ["off"] + [token for token in requested if token != "off"]
    seen = set()
    return [token for token in ordered if not (token in seen or seen.add(token))]


def build_method_config(base_config: Dict, token: str) -> Dict:
    """A deep-copied config with dynamic_range set for one method token."""
    method, tone_map = METHOD_SPECS[token]
    config = copy.deepcopy(base_config)
    adaptive = config.setdefault("adaptive_timelapse", {})
    adaptive["dynamic_range"] = {
        "method": method,
        "tone_map": {"enabled": tone_map},
    }
    return config


def daemon_is_active() -> bool:
    """Whether raspilapse.service currently owns the camera."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "raspilapse"],
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False  # No systemd: the camera-busy error will say the rest.
    return result.returncode == 0


def meter_scene(config_path: str) -> Dict:
    """One auto-exposure pass; returns the settings every method will use.

    AE and AWB run free for a couple of seconds, then the sensor's own
    report of what it settled on becomes the shared manual settings -- the
    same scene, the same exposure, for every method. Deterministic per run,
    no controller state involved.
    """
    camera_config = CameraConfig(config_path)
    capture = ImageCapture(camera_config)
    capture.initialize_camera(manual_controls={"ae_enable": True, "awb_enable": True})
    try:
        metadata = {}
        for _ in range(_METERING_FRAMES):
            request = capture.picam2.capture_request()
            try:
                metadata = request.get_metadata()
            finally:
                request.release()
        exposure_us = int(metadata.get("ExposureTime", 10_000))
        gain = float(metadata.get("AnalogueGain", 1.0))
        colour_gains = metadata.get("ColourGains") or (2.0, 1.8)
        return {
            "AeEnable": 0,
            "ExposureTime": exposure_us,
            "AnalogueGain": gain,
            "AwbEnable": 0,
            "ColourGains": tuple(colour_gains),
        }
    finally:
        capture.close()


def light_mode_for(exposure_us: int) -> str:
    """A coarse day/transition call for the dispatcher's benefit.

    drtest never claims night -- the point is to exercise the methods, and
    night is where they all deliberately stand down. AE-metered exposures
    are short by construction (AE cannot exceed the frame duration), so
    "day" is honest for anything fast and "transition" for the rest.
    """
    return "day" if exposure_us < 100_000 else "transition"


def brightness_stats(image_path: str) -> Tuple[float, float, float]:
    """Mean, p5 and p95 of the frame's luminance, for the summary table."""
    from PIL import Image

    with Image.open(image_path) as image:
        histogram = image.convert("L").histogram()
    total = sum(histogram)
    if total == 0:
        return (0.0, 0.0, 0.0)
    mean = sum(level * count for level, count in enumerate(histogram)) / total
    running, p5, p95 = 0, 0.0, 255.0
    for level, count in enumerate(histogram):
        previous = running
        running += count
        if previous < total * 0.05 <= running:
            p5 = float(level)
        if previous < total * 0.95 <= running:
            p95 = float(level)
    return (mean, p5, p95)


def run_method(
    config_path: str,
    base_config: Dict,
    token: str,
    settings: Dict,
    outdir: Path,
    stamp: str,
) -> Optional[Dict]:
    """Capture one labelled frame with one method. Returns its summary row."""
    method_config = build_method_config(base_config, token)
    dr = DynamicRange.from_config(method_config)
    if dr.label() == "off" and token != "off":
        print(f"  {token}: not available on this camera (see the warning above), skipping")
        return None

    mode = light_mode_for(settings["ExposureTime"])

    # A private CameraConfig pointed at the outdir with a label filename, so
    # every method lands exactly where the comparison wants it.
    camera_config = CameraConfig(config_path)
    camera_config.config["output"]["directory"] = str(outdir)
    camera_config.config["output"]["organize_by_date"] = False
    camera_config.config["output"]["filename_pattern"] = f"{stamp}_{token.replace('+', '_')}.jpg"
    camera_config.config["system"][
        "metadata_filename"
    ] = f"{stamp}_{token.replace('+', '_')}_metadata.json"

    capture = ImageCapture(camera_config, post_process=dr.build_post_process(method_config))
    started = time.monotonic()
    try:
        capture.initialize_camera(manual_controls=settings, **dr.pre_open(mode))
        image_path, _ = dr.capture_frame(
            capture,
            mode=mode,
            settings=settings,
            extra_metadata={"dr_method": dr.label()},
        )
    except Exception as e:
        print(f"  {token}: FAILED ({e})")
        logger.error(f"drtest method {token} failed: {e}", exc_info=True)
        return None
    finally:
        capture.close()
        dr.shutdown()
    elapsed = time.monotonic() - started

    mean, p5, p95 = brightness_stats(image_path)
    row = {
        "method": token,
        "seconds": elapsed,
        "kb": Path(image_path).stat().st_size // 1024,
        "mean": mean,
        "p5": p5,
        "p95": p95,
        "path": image_path,
    }
    if token.startswith("fusion") and capture.last_settle_frames:
        row["settle_frames"] = list(capture.last_settle_frames)
    return row


def print_summary(rows: List[Dict]) -> None:
    """The comparison table, plus the measured numbers the guards assume."""
    print()
    print(f"{'method':<12} {'time':>7} {'size':>8} {'mean':>6} {'p5':>5} {'p95':>5}")
    for row in rows:
        print(
            f"{row['method']:<12} {row['seconds']:>6.1f}s {row['kb']:>6}KB "
            f"{row['mean']:>6.1f} {row['p5']:>5.0f} {row['p95']:>5.0f}"
        )
    for row in rows:
        if "settle_frames" in row:
            print(f"\nfusion settle frames per bracket: {row['settle_frames']}")
    print(f"\nFrames: {Path(rows[0]['path']).parent}/")


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for raspilapse-drtest."""
    parser = argparse.ArgumentParser(
        description="Capture the same scene with each dynamic-range method, side by side.",
        epilog="Stop the daemon first: sudo systemctl stop raspilapse",
    )
    parser.add_argument("-c", "--config", default="config/config.yml", help="Config file path")
    parser.add_argument(
        "--methods",
        default=DEFAULT_METHODS,
        help=f"Comma-separated methods to trial (default: {DEFAULT_METHODS}; "
        f"also available: fusion+tm)",
    )
    parser.add_argument(
        "--outdir",
        default=None,
        help="Where the labelled frames go (default: <output.directory>/drtest)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Rounds to run (default 1)")
    args = parser.parse_args(argv)

    try:
        methods = parse_methods(args.methods)
    except ValueError as e:
        parser.error(str(e))

    if daemon_is_active():
        print("raspilapse.service is running and owns the camera. Stop it first:")
        print("    sudo systemctl stop raspilapse")
        print("and start it again afterwards:")
        print("    sudo systemctl start raspilapse")
        return 1

    base_config = CameraConfig(args.config).config
    outdir = Path(args.outdir or Path(base_config["output"]["directory"]) / "drtest")
    outdir.mkdir(parents=True, exist_ok=True)

    for round_index in range(max(args.repeat, 1)):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        print("Metering the scene (auto-exposure)...")
        try:
            settings = meter_scene(args.config)
        except Exception as e:
            message = str(e).lower()
            if "in use" in message or "busy" in message or "acquire" in message:
                print("The camera is busy -- is the daemon (or another tool) still running?")
                print("    sudo systemctl stop raspilapse")
                return 1
            raise
        print(
            f"Base: {settings['ExposureTime'] / 1000:.1f}ms, "
            f"gain {settings['AnalogueGain']:.2f}, "
            f"WB [{settings['ColourGains'][0]:.2f}, {settings['ColourGains'][1]:.2f}]"
        )

        rows = []
        for token in methods:
            print(f"Capturing: {token}")
            row = run_method(args.config, base_config, token, settings, outdir, stamp)
            if row:
                rows.append(row)
        if rows:
            print_summary(rows)
        if args.repeat > 1 and round_index < args.repeat - 1:
            time.sleep(2)

    print("\nRemember: sudo systemctl start raspilapse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
