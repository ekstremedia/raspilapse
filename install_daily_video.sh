#!/bin/bash

# Raspilapse Daily Video Service Installation Script
# This script installs a systemd timer that automatically creates
# a timelapse video from the last 24 hours every day at 04:00

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_header() {
    echo
    echo -e "${BOLD}${CYAN}============================================${NC}"
    echo -e "${BOLD}${CYAN}  Raspilapse Daily Video Service Installer${NC}"
    echo -e "${BOLD}${CYAN}============================================${NC}"
    echo
}

# Check if running as root (for sudo commands)
check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_error "This script should not be run as root!"
        print_info "Please run as regular user (pi). The script will use sudo when needed."
        exit 1
    fi
}

# Print header
print_header

# Check root
check_root

# Check if running from correct directory
if [[ ! -f "src/make_timelapse.py" ]]; then
    print_error "Please run this script from the raspilapse project root directory"
    print_info "cd /home/pi/raspilapse && ./install_daily_video.sh"
    exit 1
fi

print_info "Installing Raspilapse Daily Video Generation Service..."
echo

# Create output directory if it doesn't exist
VIDEO_DIR="/var/www/html/videos"
print_info "Creating video output directory: $VIDEO_DIR"
sudo mkdir -p "$VIDEO_DIR"
sudo chown pi:pi "$VIDEO_DIR"
print_success "Video directory ready"

# Copy service files to systemd directory
print_info "Installing systemd service files..."
sudo cp systemd/raspilapse-daily-video.service /etc/systemd/system/
sudo cp systemd/raspilapse-daily-video.timer /etc/systemd/system/
print_success "Service files installed"

# Reload systemd daemon
print_info "Reloading systemd daemon..."
sudo systemctl daemon-reload
print_success "Systemd daemon reloaded"

# Enable the timer (not the service - the timer triggers the service)
print_info "Enabling daily video timer..."
sudo systemctl enable raspilapse-daily-video.timer
print_success "Timer enabled"

# Start the timer
print_info "Starting daily video timer..."
sudo systemctl start raspilapse-daily-video.timer
print_success "Timer started"

echo
print_header
print_success "Daily video service installed successfully!"
echo

# Show timer status
echo -e "${BOLD}Timer Status:${NC}"
sudo systemctl status raspilapse-daily-video.timer --no-pager

echo
echo -e "${BOLD}Next scheduled run:${NC}"
sudo systemctl list-timers raspilapse-daily-video.timer --no-pager

echo
echo -e "${BOLD}${CYAN}Configuration:${NC}"
echo "  • Videos will be generated daily at 04:00"
echo "  • Output directory: ${VIDEO_DIR}"
echo "  • Video format: {project_name}_daily_YYYY-MM-DD.mp4"
echo "  • Time range: Last 24 hours"

echo
echo -e "${BOLD}${CYAN}Useful Commands:${NC}"
echo "  • Check timer status:    ${BOLD}sudo systemctl status raspilapse-daily-video.timer${NC}"
echo "  • Check service logs:     ${BOLD}sudo journalctl -u raspilapse-daily-video.service -f${NC}"
echo "  • Run manually now:       ${BOLD}sudo systemctl start raspilapse-daily-video.service${NC}"
echo "  • Disable daily videos:   ${BOLD}sudo systemctl disable --now raspilapse-daily-video.timer${NC}"
echo "  • Change schedule:        ${BOLD}sudo systemctl edit raspilapse-daily-video.timer${NC}"

echo
print_info "To test the service now, run:"
echo -e "    ${BOLD}sudo systemctl start raspilapse-daily-video.service${NC}"
echo
print_info "Then check the output in ${VIDEO_DIR}"
echo