#!/bin/bash
# Raspilapse Test Script
# Comprehensive testing for camera, configuration, and service

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# Project root directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo -e "${BOLD}${CYAN}================================${RESET}"
echo -e "${BOLD}${CYAN}  Raspilapse Test Suite${RESET}"
echo -e "${BOLD}${CYAN}================================${RESET}\n"

# Test 1: Check Python dependencies
echo -e "${BOLD}1. Checking Python dependencies...${RESET}"
MISSING_DEPS=()

check_python_package() {
    if ! python3 -c "import $1" 2>/dev/null; then
        MISSING_DEPS+=("$1")
        echo -e "  ${RED}✗${RESET} $1 not found"
    else
        echo -e "  ${GREEN}✓${RESET} $1"
    fi
}

check_python_package "picamera2"
check_python_package "yaml"
check_python_package "PIL"
check_python_package "numpy"

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo -e "\n${YELLOW}⚠  Missing dependencies:${RESET} ${MISSING_DEPS[*]}"
    echo -e "   Install with: sudo apt install python3-picamera2 python3-pil python3-numpy python3-yaml"
else
    echo -e "${GREEN}All dependencies installed${RESET}"
fi
echo ""

# Test 2: Check configuration file
echo -e "${BOLD}2. Checking configuration...${RESET}"
if [ -f "config/config.yml" ]; then
    echo -e "  ${GREEN}✓${RESET} config/config.yml exists"

    # Validate YAML syntax
    if python3 -c "import yaml; yaml.safe_load(open('config/config.yml'))" 2>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} Valid YAML syntax"
    else
        echo -e "  ${RED}✗${RESET} Invalid YAML syntax"
    fi
else
    echo -e "  ${RED}✗${RESET} config/config.yml not found"
fi
echo ""

# Test 3: Check camera hardware
echo -e "${BOLD}3. Checking camera hardware...${RESET}"
if command -v rpicam-still &> /dev/null; then
    echo -e "  ${GREEN}✓${RESET} rpicam-still available"

    # Try to detect camera
    if timeout 3 rpicam-still --list-cameras &>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} Camera detected"
    else
        echo -e "  ${YELLOW}⚠${RESET}  Camera detection failed (may need reboot)"
    fi
else
    echo -e "  ${YELLOW}⚠${RESET}  rpicam-still not found (optional)"
fi
echo ""

# Test 4: Check output directories
echo -e "${BOLD}4. Checking output directories...${RESET}"
OUTPUT_DIR=$(python3 -c "import yaml; print(yaml.safe_load(open('config/config.yml'))['output']['directory'])" 2>/dev/null || echo "")

if [ -n "$OUTPUT_DIR" ]; then
    if [ -d "$OUTPUT_DIR" ]; then
        echo -e "  ${GREEN}✓${RESET} Output directory exists: $OUTPUT_DIR"

        # Check write permissions
        if [ -w "$OUTPUT_DIR" ]; then
            echo -e "  ${GREEN}✓${RESET} Directory is writable"
        else
            echo -e "  ${RED}✗${RESET} Directory is not writable"
        fi
    else
        echo -e "  ${YELLOW}⚠${RESET}  Output directory does not exist: $OUTPUT_DIR"
        echo -e "     (will be created on first capture)"
    fi
else
    echo -e "  ${YELLOW}⚠${RESET}  Could not read output directory from config"
fi

# Check metadata directory
if [ -d "metadata" ]; then
    echo -e "  ${GREEN}✓${RESET} Metadata directory exists"
else
    echo -e "  ${YELLOW}⚠${RESET}  Metadata directory does not exist (will be created)"
fi
echo ""

# Test 5: Check service status
echo -e "${BOLD}5. Checking service status...${RESET}"
if systemctl list-unit-files | grep -q "raspilapse.service"; then
    echo -e "  ${GREEN}✓${RESET} Service is installed"

    # Check if service is enabled
    if systemctl is-enabled raspilapse.service &>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} Service is enabled (starts on boot)"
    else
        echo -e "  ${YELLOW}○${RESET} Service is not enabled"
    fi

    # Check if service is running
    if systemctl is-active raspilapse.service &>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} Service is running"
    else
        echo -e "  ${YELLOW}○${RESET} Service is stopped"
    fi
else
    echo -e "  ${YELLOW}○${RESET} Service is not installed"
    echo -e "     Run: sudo ./install_service.sh"
fi
echo ""

# Test 6: Test capture (optional)
echo -e "${BOLD}6. Test capture (optional)${RESET}"
echo -e "${DIM}   Skip this test if camera is in use${RESET}"
read -p "   Run test capture? (y/N): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "  ${CYAN}Running test capture...${RESET}"

    if python3 src/auto_timelapse.py --test; then
        echo -e "  ${GREEN}✓${RESET} Test capture successful"

        # Find the test image
        if [ -f "metadata/test_shot.jpg" ]; then
            echo -e "  ${GREEN}✓${RESET} Test image created: metadata/test_shot.jpg"
        fi
    else
        echo -e "  ${RED}✗${RESET} Test capture failed"
    fi
else
    echo -e "  ${YELLOW}⊘${RESET} Test skipped"
fi
echo ""

# Test 7: Display full status
echo -e "${BOLD}7. System Status${RESET}"
echo -e "${DIM}   Running status script...${RESET}\n"

if [ -f "src/status.py" ]; then
    python3 src/status.py
else
    echo -e "  ${YELLOW}⚠${RESET}  Status script not found"
fi

# Summary
echo -e "\n${BOLD}${CYAN}================================${RESET}"
echo -e "${BOLD}${CYAN}  Test Complete${RESET}"
echo -e "${BOLD}${CYAN}================================${RESET}\n"

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠  Action required:${RESET} Install missing dependencies"
    exit 1
else
    echo -e "${GREEN}✓ All tests passed${RESET}\n"
    exit 0
fi
