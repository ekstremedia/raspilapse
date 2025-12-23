# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.4] - 2025-12-23

### Added
- **Diagnostic metadata**: Each captured frame now includes comprehensive diagnostic data
  - Mode information (day/night/transition)
  - Raw vs smoothed lux values
  - Target vs interpolated exposure/gain (shows smoothing in action)
  - Transition position (0-1) when in transition mode
  - Hysteresis state tracking
- **Image brightness analysis**: Automatic analysis of captured images
  - Mean/median brightness (0-255)
  - Brightness percentiles (5th, 25th, 75th, 95th)
  - Underexposed/overexposed pixel percentages
  - Helps diagnose exposure issues

### Fixed
- **Continuous exposure calculation**: Exposure now adjusts continuously with lux
  - Formula: `exposure = 20 / lux` (inverse relationship)
  - Previously used threshold-based fixed values causing sudden jumps
  - At lux 400: target is now ~50ms instead of fixed 10ms
  - Prevents dark images during cloudy winter days

### Documentation
- Updated `TRANSITION_SMOOTHING.md` with diagnostic metadata section
- Updated `ADAPTIVE_TIMELAPSE_FLOW.md` with diagnostic information
- Added debugging commands for exposure analysis

## [1.0.3] - 2025-12-23

### Added
- **Date-organized video output**: Videos now saved to `YYYY/MM/` subdirectories
  - New config option `video.organize_by_date: true`
  - New config option `video.date_format: "%Y/%m"`
  - Example: `/var/www/html/videos/2025/12/kringelen_nord_daily_2025-12-23.mp4`
- **Memory-optimized encoding**: Prevents OOM kills on 4K video encoding
  - New config option `video.codec.preset: "ultrafast"`
  - New config option `video.codec.threads: 2`
  - Safe for Raspberry Pi with 4GB RAM encoding 4K video

### Changed
- **Service timeout**: Changed from 10 minutes to `infinity`
  - Prevents ffmpeg being killed mid-encode
  - Videos now complete regardless of encoding time
- **Default video directory**: Now `/var/www/html/videos` (absolute path)
- **Default CRF**: Changed from 20 to 23 for smaller file sizes

### Fixed
- **Video corruption**: Fixed "moov atom not found" errors
  - Caused by systemd killing ffmpeg after 10-minute timeout
  - Now uses unlimited timeout for complete encoding
- **OOM kills during 4K encoding**: Fixed by using ultrafast preset and 2 threads
  - Previously ffmpeg used too much RAM and was killed by OOM killer

### Documentation
- Updated `TIMELAPSE_VIDEO.md` with new configuration options
- Updated `DAILY_VIDEO.md` with performance notes and troubleshooting
- Added OOM troubleshooting guide
- Added memory usage guidance for 4K encoding

## [1.0.0] - 2025-11-09

### 🎉 First Stable Release!

Raspilapse v1.0.0 is production-ready for year-long operation.

### Added
- **Systemd Cleanup Service**: Automatic old image deletion
  - `raspilapse-cleanup.timer` runs daily at 01:00 AM
  - Configurable retention period (default 7 days)
  - Prevents disk from filling up
  - Logs cleanup statistics
- **Daily Video Generation**: Automated timelapse compilation
  - `raspilapse-daily-video.timer` runs at 00:04 AM
  - Creates videos from previous day's images
  - H.264 encoding with configurable quality
- **Weather Data Integration**: Netatmo API support
  - Fetch outdoor temperature, humidity, wind, rain
  - Display in image overlays
  - Configurable endpoint and caching
- **Analysis Tools**: Beautiful graphs and Excel export
  - Dark-themed lux analysis graphs
  - Exposure, gain, temperature tracking
  - Excel export with statistics and hourly averages
- **Status Display**: Colored terminal status output
  - Service status monitoring
  - Configuration summary
  - Recent captures with timing
  - Average interval calculation
- **Comprehensive Documentation** (18 guides):
  - `SERVICES_OVERVIEW.md` - Complete systemd reference
  - `LONG_TERM_STABILITY.md` - Year-long operation guide
  - `MONITORING_SETUP.md` - Monitoring and alerting
  - `YEAR_LONG_CHECKLIST.md` - Monthly maintenance
  - `SETUP_COMPLETE.md` - Post-installation summary
- **Monitoring Scripts**: Automated health checks
  - Disk space monitoring
  - Service health verification
  - Capture rate tracking

### Changed
- **Project Structure Reorganization** 🏗️
  - All documentation moved to `docs/` folder
  - All scripts consolidated in `scripts/` folder
  - Clean root directory (only essential config files)
  - Professional, maintainable structure
- **Script Paths** (Breaking Change):
  - `install_service.sh` → `scripts/install.sh`
  - `uninstall_service.sh` → `scripts/uninstall.sh`
  - `test.sh` → `scripts/test.sh`
- **Version**: Updated from 0.9.0-beta to 1.0.0
- **README**: Updated with new paths and structure

### Fixed
- Metadata accumulation in `metadata/` folder
  - Test shots now use fixed filenames (overwritten)
  - No more thousands of test metadata files
- Documentation references throughout project
- Service file locations and references

### Removed
- Obsolete test scripts from root directory
- Duplicate service files
- Outdated documentation fragments

### Production Ready
- ✅ **221 passing tests** (1 skipped)
- ✅ **No memory leaks** - Stable at ~150MB RSS
- ✅ **Low CPU usage** - 4% average
- ✅ **Tested for days** of continuous operation
- ✅ **Auto-recovery** from failures
- ✅ **Disk space management** built-in
- ✅ **Year-long stability** verified

### Migration from 0.9.0-beta
1. Pull latest changes: `git pull origin main`
2. Update service: `sudo systemctl stop raspilapse && ./scripts/install.sh`
3. Documentation moved to `docs/` folder
4. No configuration changes required

See [docs/V1_RELEASE_NOTES.md](docs/V1_RELEASE_NOTES.md) for complete release details.

---

## [0.9.0-beta] - 2025-11-05

### Added
- **Image Overlay System**: Professional text overlays with camera settings and metadata
  - Configurable positioning (corners, top-bar, custom)
  - Localized datetime formatting (Norwegian, English, etc.)
  - Gradient transparent backgrounds
  - Resolution-independent scaling
  - Color gains tuple display
  - Standalone `apply_overlay.py` script for batch processing
- **Web Integration**: Automatic symlink to latest captured image
  - Configurable symlink path for web servers
  - Perfect for live status pages
- **Enhanced Testing**: Comprehensive test suite with 64 unit tests
  - Overlay system tests
  - Adaptive timelapse tests
  - Symlink functionality tests
  - Full CI/CD integration
- **Project Metadata**:
  - MIT License file
  - Version tracking system
  - pyproject.toml for modern packaging
  - Enhanced README with badges

### Changed
- Overlay now reads configuration dynamically instead of hardcoding values
- Improved error handling for symlink creation
- Better logging for overlay operations

### Fixed
- Black code formatting across all files
- CI pipeline dependencies (added Pillow to requirements.txt)
- Import errors in test modules

## [0.8.0-beta] - 2025-11-04

### Added
- Image overlay prototype with basic text rendering
- Font loading with fallback options
- Background transparency support

## [0.7.0-beta] - 2025-11-03

### Added
- **Adaptive Timelapse**: Automatic exposure adjustment based on ambient light
  - Day mode: Optimized for bright sunlight
  - Night mode: Long exposures up to 20 seconds for stars/aurora
  - Transition mode: Smooth adjustment during dawn/dusk
  - Test shot system for light measurement
- Lux calculation from camera metadata
- Light mode detection with configurable thresholds

### Changed
- Filename patterns now support timestamp formatting
- Test shots stored in metadata/ folder (overwritten, not accumulated)
- Project structure reorganization

## [0.6.0-beta] - 2025-11-02

### Added
- **Long Exposure Optimization**: Dramatically improved capture speed
  - FrameDurationLimits configuration for proper frame period
  - Buffer management (buffer_count=3, queue=False)
  - Non-blocking metadata capture with capture_request()
  - Fast camera shutdown between configuration changes
  - AWB locking for night mode
- Comprehensive documentation in CLAUDE.md

### Changed
- Capture time for 20s exposures reduced from 99-124s to 18-20s (5-7x faster!)
- Camera now properly closes between test shots and timelapse captures

### Fixed
- Long exposure blocking delays
- Camera state conflicts with multiple instances
- Auto white balance slowdown during long exposures

## [0.5.0-beta] - 2025-11-01

### Added
- **Comprehensive Logging System**
  - Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  - Automatic log rotation based on file size
  - Both console and file output
  - Script-specific log files
- Metadata capture with every image
- Detailed error tracking and debugging

### Changed
- Improved error messages throughout the codebase
- Better structured logging configuration

## [0.4.0-beta] - 2025-10-30

### Added
- Initial public beta release
- YAML-based configuration system
- Camera configuration module
- Image capture module
- Basic timelapse functionality
- Support for Camera V3 and V2
- Multiple resolution support
- Image transforms (flip/rotate)
- Camera controls (exposure, gain, white balance)
- Test suite with pytest
- CI/CD with GitHub Actions

### Documentation
- Installation guide (INSTALL.md)
- Usage guide (USAGE.md)
- Technical reference (CLAUDE.md)
- README with examples

---

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):
- **0.x.x-beta**: Beta releases (current)
- **1.0.0**: First stable release
- **MAJOR**: Incompatible API changes
- **MINOR**: New functionality (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

---

**[Compare versions on GitHub](https://github.com/ekstremedia/raspilapse/releases)**
