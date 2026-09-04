#!/bin/bash
# APIloop Uninstall Script
set -e

echo "=== APIloop Uninstallation ==="
echo ""

# Remove virtual environment
VENV_DIR="${VENV_DIR:-$HOME/.local/share/apiloop/venv}"
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "Removed virtual environment: $VENV_DIR"
fi

# Remove config (optional - backup first)
CONFIG_DIR="$HOME/.config/apiloop"
if [ -d "$CONFIG_DIR" ]; then
    echo "WARNING: Config directory will be removed: $CONFIG_DIR"
    read -p "Remove config? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$CONFIG_DIR"
        echo "Removed config directory"
    else
        echo "Keeping config directory"
    fi
fi

# Remove CLI entry point
if command -v apiloop &> /dev/null; then
    echo "Note: CLI entry point may need manual removal from PATH"
fi

echo ""
echo "✓ APIloop uninstalled"
echo ""
echo "To complete cleanup, remove the project directory:"
echo "  rm -rf ~/apiloop"
