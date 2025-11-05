#!/bin/bash
# Raspilapse Service Installation Script
# This script installs the raspilapse service to run continuously as a systemd daemon

set -e  # Exit on error

echo "====================================================================="
echo "  Raspilapse Service Installation"
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

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
USER=$(whoami)

echo -e "${GREEN}✓${NC} Running as user: $USER"
echo -e "${GREEN}✓${NC} Installation directory: $SCRIPT_DIR"
echo ""

# Step 1: Create directories
echo "Step 1: Creating image directories..."
sudo mkdir -p /var/www/html/images
sudo chown -R $USER:www-data /var/www/html/images
sudo chmod -R 775 /var/www/html/images
echo -e "${GREEN}✓${NC} Created /var/www/html/images"

# Create logs directory if it doesn't exist
mkdir -p "$SCRIPT_DIR/logs"
echo -e "${GREEN}✓${NC} Created logs directory"
echo ""

# Step 2: Update service file with correct user and paths
echo "Step 2: Configuring service file..."
SERVICE_FILE="$SCRIPT_DIR/raspilapse.service"

# Create service file with correct paths
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Raspilapse Continuous Timelapse Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/src/auto_timelapse.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}✓${NC} Service file configured for user: $USER"
echo ""

# Step 3: Install systemd service
echo "Step 3: Installing systemd service..."
sudo cp "$SERVICE_FILE" /etc/systemd/system/raspilapse.service
sudo systemctl daemon-reload
echo -e "${GREEN}✓${NC} Service installed"
echo ""

# Step 4: Enable service (start on boot)
echo "Step 4: Enabling service to start on boot..."
sudo systemctl enable raspilapse.service
echo -e "${GREEN}✓${NC} Service enabled"
echo ""

# Summary
echo "====================================================================="
echo "  Installation Complete!"
echo "====================================================================="
echo ""
echo "Service Management Commands:"
echo ""
echo -e "  ${GREEN}Start service:${NC}      sudo systemctl start raspilapse"
echo -e "  ${GREEN}Stop service:${NC}       sudo systemctl stop raspilapse"
echo -e "  ${GREEN}Restart service:${NC}    sudo systemctl restart raspilapse"
echo -e "  ${GREEN}Check status:${NC}       sudo systemctl status raspilapse"
echo -e "  ${GREEN}View logs:${NC}          sudo journalctl -u raspilapse -f"
echo -e "  ${GREEN}Disable autostart:${NC}  sudo systemctl disable raspilapse"
echo ""
echo "Image Storage:"
echo "  Location: /var/www/html/images/YYYY/MM/DD/"
echo "  Example:  /var/www/html/images/2025/11/05/kringelen_2025_11_05_14_30_00.jpg"
echo ""
echo "Configuration:"
echo "  Edit config: nano $SCRIPT_DIR/config/config.yml"
echo "  After editing, restart service: sudo systemctl restart raspilapse"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "  1. Review/edit config: nano $SCRIPT_DIR/config/config.yml"
echo "  2. Start the service: sudo systemctl start raspilapse"
echo "  3. Check status: sudo systemctl status raspilapse"
echo "  4. View logs: sudo journalctl -u raspilapse -f"
echo ""
echo "====================================================================="
