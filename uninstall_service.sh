#!/bin/bash
# Raspilapse Service Uninstallation Script

set -e

echo "======================================================================"
echo "  Raspilapse Service Uninstallation"
echo "======================================================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if service exists
if ! systemctl list-unit-files | grep -q raspilapse.service; then
    echo -e "${YELLOW}Service not found. Nothing to uninstall.${NC}"
    exit 0
fi

echo "This will:"
echo "  - Stop the raspilapse service"
echo "  - Disable autostart on boot"
echo "  - Remove the service file"
echo ""
echo -e "${YELLOW}Note: Images in /var/www/html/images/ will NOT be deleted${NC}"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 1
fi

echo ""
echo "Stopping service..."
sudo systemctl stop raspilapse.service || true
echo -e "${GREEN}✓${NC} Service stopped"

echo "Disabling service..."
sudo systemctl disable raspilapse.service || true
echo -e "${GREEN}✓${NC} Service disabled"

echo "Removing service file..."
sudo rm -f /etc/systemd/system/raspilapse.service
sudo systemctl daemon-reload
echo -e "${GREEN}✓${NC} Service file removed"

echo ""
echo "======================================================================"
echo "  Uninstallation Complete!"
echo "======================================================================"
echo ""
echo "The service has been removed."
echo ""
echo "To reinstall, run:"
echo "  ./install_service.sh"
echo ""
