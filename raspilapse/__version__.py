"""Version information for Raspilapse."""

__version__ = "1.6.0"
__author__ = "Terje Nesthus"
__email__ = "terje@ekstremedia.no"
__license__ = "MIT"
__description__ = "Continuous adaptive timelapse capture for the Raspberry Pi camera"
__url__ = "https://github.com/ekstremedia/raspilapse"

# Version history:
# 1.6.0 - Fresh-install audit: overlay defaults, midnight/DST filing, ffmpeg timeouts,
#         retention by covered date, camera-failure exits, documentation audit
# 1.5.0 - Network watchdog, capture telemetry, video retention, faster 4K encode
# 1.4.0 - Removed the ML exposure system, single installer, logging and database overhaul
#         (tagged only at 1.5.0; 1.4.0 shipped as code but was never released)
# 1.1.0 - Documentation audit, production cleanup, removed legacy migration code
# 1.0.9 - Database graph generator with Gaussian smoothing and temperature gradients
# 1.0.8 - Fixed EV safety clamp, ML-based adaptive exposure, SQLite database storage
# 0.9.0-beta - Feature-complete beta with adaptive timelapse and overlay system
# 0.8.0-beta - Added image overlay system with localization
# 0.7.0-beta - Added adaptive timelapse with day/night/transition modes
# 0.6.0-beta - Long exposure optimization
# 0.5.0-beta - Comprehensive logging system
# 0.4.0-beta - Initial release
