# Raspilapse Documentation

Complete documentation for Raspilapse v1.0.0

## 📖 Getting Started

### Installation & Setup
- **[INSTALL.md](INSTALL.md)** - Complete installation guide
- **[SERVICE.md](SERVICE.md)** - Systemd service setup
- **[USAGE.md](USAGE.md)** - Basic usage and configuration
- **[SETUP_COMPLETE.md](SETUP_COMPLETE.md)** - Post-installation checklist

### Quick Start

1. Read **INSTALL.md** for hardware and software requirements
2. Follow **SERVICE.md** to set up continuous operation
3. Check **SETUP_COMPLETE.md** to verify everything works
4. Review **USAGE.md** for daily operation

## 🎥 Features & Configuration

### Core Features
- **[DAILY_VIDEO.md](DAILY_VIDEO.md)** - Automatic daily video generation
- **[OVERLAY.md](OVERLAY.md)** - Image overlay configuration
- **[WEATHER.md](WEATHER.md)** - Weather data integration
- **[TIMELAPSE_VIDEO.md](TIMELAPSE_VIDEO.md)** - Video compilation guide
- **[ADAPTIVE_TIMELAPSE_FLOW.md](ADAPTIVE_TIMELAPSE_FLOW.md)** - Day/night automation

### Advanced Topics
- **[CLAUDE.md](CLAUDE.md)** - Technical reference (Picamera2)
- **[SIMPLE_OVERLAY.md](SIMPLE_OVERLAY.md)** - Simple overlay guide

## 🔧 System Administration

### Service Management
- **[SERVICES_OVERVIEW.md](SERVICES_OVERVIEW.md)** - Complete systemd reference
- **[LONG_TERM_STABILITY.md](LONG_TERM_STABILITY.md)** - Year-long operation guide
- **[MONITORING_SETUP.md](MONITORING_SETUP.md)** - Monitoring and alerting
- **[YEAR_LONG_CHECKLIST.md](YEAR_LONG_CHECKLIST.md)** - Monthly maintenance tasks

### Troubleshooting
Check these guides for common issues:
1. **INSTALL.md** - Installation problems
2. **SERVICE.md** - Service not starting
3. **LONG_TERM_STABILITY.md** - Performance issues
4. **SERVICES_OVERVIEW.md** - Service management

## 👨‍💻 Development

### Contributing
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Contribution guidelines
- **[MAINTAINER.md](MAINTAINER.md)** - Maintainer's guide
- **[BLACK_FORMATTING_GUIDE.md](BLACK_FORMATTING_GUIDE.md)** - Code style guide
- **[COVERAGE_IMPROVEMENTS.md](COVERAGE_IMPROVEMENTS.md)** - Test coverage guide

## 📋 Release Information

- **[V1_RELEASE_NOTES.md](V1_RELEASE_NOTES.md)** - v1.0.0 release details
- **[../CHANGELOG.md](../CHANGELOG.md)** - Full version history

## 🎯 Documentation by Topic

### Hardware
- [INSTALL.md](INSTALL.md#hardware-requirements) - Requirements
- [CLAUDE.md](CLAUDE.md) - Picamera2 reference

### Configuration
- [USAGE.md](USAGE.md) - Basic settings
- [OVERLAY.md](OVERLAY.md) - Text overlays
- [WEATHER.md](WEATHER.md) - Weather integration

### Operation
- [SERVICE.md](SERVICE.md) - Running as service
- [DAILY_VIDEO.md](DAILY_VIDEO.md) - Daily videos
- [ADAPTIVE_TIMELAPSE_FLOW.md](ADAPTIVE_TIMELAPSE_FLOW.md) - Adaptive mode

### Maintenance
- [SERVICES_OVERVIEW.md](SERVICES_OVERVIEW.md) - Service commands
- [MONITORING_SETUP.md](MONITORING_SETUP.md) - Health checks
- [YEAR_LONG_CHECKLIST.md](YEAR_LONG_CHECKLIST.md) - Monthly tasks
- [LONG_TERM_STABILITY.md](LONG_TERM_STABILITY.md) - Long-term operation

### Development
- [CONTRIBUTING.md](CONTRIBUTING.md) - How to contribute
- [MAINTAINER.md](MAINTAINER.md) - Release process
- [BLACK_FORMATTING_GUIDE.md](BLACK_FORMATTING_GUIDE.md) - Code style
- [COVERAGE_IMPROVEMENTS.md](COVERAGE_IMPROVEMENTS.md) - Testing

## 📊 Documentation Statistics

- **Total Guides:** 19
- **Installation Guides:** 4
- **Feature Guides:** 6
- **Admin Guides:** 4
- **Development Guides:** 4
- **Release Docs:** 1

## 🔗 Quick Links

- **Main README:** [../README.md](../README.md)
- **Installation Script:** [../scripts/install.sh](../scripts/install.sh)
- **Configuration File:** [../config/config.yml](../config/config.yml)
- **Source Code:** [../src/](../src/)
- **Tests:** [../tests/](../tests/)

## 💡 Help & Support

1. **Check the docs first** - Most questions are answered here
2. **Run the test script** - `./scripts/test.sh` diagnoses issues
3. **View service logs** - `journalctl -u raspilapse.service`
4. **GitHub Issues** - https://github.com/ekstremedia/raspilapse/issues

## 📝 Documentation Updates

This documentation is for **Raspilapse v1.0.0**.

Last updated: November 9, 2025

---

**Need quick help?** Start with [SETUP_COMPLETE.md](SETUP_COMPLETE.md) for a summary of everything!
