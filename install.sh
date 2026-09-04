#!/bin/bash
# APIloop Install Script
set -e

echo "=== APIloop Installation ==="
echo ""

# Detect Python
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &> /dev/null; then
    echo "ERROR: Python 3 not found"
    exit 1
fi

echo "Python: $($PYTHON --version)"

# Create virtual environment if not exists
VENV_DIR="${VENV_DIR:-~/.local/share/apiloop/venv}"
if [ ! -d "$HOME/.local/share/apiloop" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Install dependencies
echo "Installing dependencies..."
pip install --quiet -e ".[dev]"

# Create config directory
CONFIG_DIR="$HOME/.config/apiloop"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

echo ""
echo "✓ APIloop installed successfully"
echo ""
echo "Next steps:"
echo "  1. Configure providers: apiloop provider add <name>"
echo "  2. Add credentials:     apiloop provider credential add <name> --api-key sk-..."
echo "  3. Test setup:         apiloop doctor"
echo "  4. Start gateway:      apiloop start"
echo ""
