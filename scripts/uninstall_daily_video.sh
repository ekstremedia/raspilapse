#!/bin/bash

# Raspilapse Daily Video Service Uninstallation Script
# This script removes the systemd timer and service for daily video generation

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
    echo -e "${BOLD}${CYAN}===============================================${NC}"
    echo -e "${BOLD}${CYAN}  Raspilapse Daily Video Service Uninstaller${NC}"
    echo -e "${BOLD}${CYAN}===============================================${NC}"
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

print_warning "This will remove the daily video generation service."
echo
read -p "Are you sure you want to continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Uninstallation cancelled."
    exit 0
fi

print_info "Uninstalling Raspilapse Daily Video Generation Service..."
echo

# Stop the timer if it's running
if systemctl is-active --quiet raspilapse-daily-video.timer; then
    print_info "Stopping daily video timer..."
    sudo systemctl stop raspilapse-daily-video.timer
    print_success "Timer stopped"
fi

# Disable the timer
if systemctl is-enabled --quiet raspilapse-daily-video.timer 2>/dev/null; then
    print_info "Disabling daily video timer..."
    sudo systemctl disable raspilapse-daily-video.timer
    print_success "Timer disabled"
fi

# Remove service files
print_info "Removing systemd service files..."
sudo rm -f /etc/systemd/system/raspilapse-daily-video.service
sudo rm -f /etc/systemd/system/raspilapse-daily-video.timer
print_success "Service files removed"

# Reload systemd daemon
print_info "Reloading systemd daemon..."
sudo systemctl daemon-reload
print_success "Systemd daemon reloaded"

echo
print_header
print_success "Daily video service uninstalled successfully!"
echo

print_info "Note: The video output directory (/var/www/html/videos) was NOT removed."
print_info "      Your existing videos are still available there."
echo

print_info "To reinstall the service later, run:"
echo -e "    ${BOLD}./install_daily_video.sh${NC}"
echo