# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned for 1.0.0
- Video compilation script
- Web interface for monitoring
- Mobile app integration
- Advanced scheduling
- Cloud storage integration

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
