#!/bin/bash
# Raspilapse Cleanup Service Installation Script
# Installs automatic image cleanup that runs daily at 2:00 AM

set -e  # Exit on error

echo "====================================================================="
echo "  Raspilapse Cleanup Service Installation"
echo "====================================================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}ERROR: Do not run this script as root or with sudo!${NC}"
    echo "Run it as your normal user (e.g., pi)"
    echo "The script will prompt for sudo when needed."
    exit 1
fi

# Get the script directory and project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER=$(whoami)

echo -e "${GREEN}✓${NC} Running as user: $USER"
echo -e "${GREEN}✓${NC} Project directory: $PROJECT_DIR"
echo ""

# Step 1: Copy service files with correct paths
echo "Step 1: Configuring cleanup service files..."

# Create temporary service file with correct paths
SERVICE_FILE="/tmp/raspilapse-cleanup.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Raspilapse Image Cleanup Service
Documentation=https://github.com/ekstremedia/raspilapse
After=network.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=/bin/bash $PROJECT_DIR/scripts/cleanup_old_images.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create temporary timer file
TIMER_FILE="/tmp/raspilapse-cleanup.timer"
cat > "$TIMER_FILE" << EOF
[Unit]
Description=Raspilapse Image Cleanup Timer
Documentation=https://github.com/ekstremedia/raspilapse
Requires=raspilapse-cleanup.service

[Timer]
# Run daily at 2:00 AM (after daily video generation at 1:00 AM)
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo -e "${GREEN}✓${NC} Service files configured"
echo ""

# Step 2: Install systemd service and timer
echo "Step 2: Installing systemd service and timer..."
sudo cp "$SERVICE_FILE" /etc/systemd/system/raspilapse-cleanup.service
sudo cp "$TIMER_FILE" /etc/systemd/system/raspilapse-cleanup.timer
sudo systemctl daemon-reload
echo -e "${GREEN}✓${NC} Service and timer installed"
echo ""

# Step 3: Enable and start timer
echo "Step 3: Enabling cleanup timer..."
sudo systemctl enable raspilapse-cleanup.timer
sudo systemctl start raspilapse-cleanup.timer
echo -e "${GREEN}✓${NC} Timer enabled and started"
echo ""

# Clean up temp files
rm -f "$SERVICE_FILE" "$TIMER_FILE"

# Summary
echo "====================================================================="
echo "  Installation Complete!"
echo "====================================================================="
echo ""
echo "Cleanup Service Configuration:"
echo "  - Deletes images older than 7 days"
echo "  - Runs daily at 2:00 AM"
echo "  - Location: /var/www/html/images"
echo ""
echo "Management Commands:"
echo ""
echo -e "  ${GREEN}Check timer status:${NC}       sudo systemctl status raspilapse-cleanup.timer"
echo -e "  ${GREEN}Check service status:${NC}     sudo systemctl status raspilapse-cleanup.service"
echo -e "  ${GREEN}View next run time:${NC}       systemctl list-timers | grep cleanup"
echo -e "  ${GREEN}Run cleanup now:${NC}          sudo systemctl start raspilapse-cleanup.service"
echo -e "  ${GREEN}View cleanup logs:${NC}        sudo journalctl -u raspilapse-cleanup.service"
echo -e "  ${GREEN}Disable timer:${NC}            sudo systemctl disable raspilapse-cleanup.timer"
echo -e "  ${GREEN}Stop timer:${NC}               sudo systemctl stop raspilapse-cleanup.timer"
echo ""
echo -e "${YELLOW}Note:${NC} The cleanup service will run daily at 2:00 AM."
echo "      You can manually run it anytime with: sudo systemctl start raspilapse-cleanup.service"
echo ""
echo "====================================================================="
