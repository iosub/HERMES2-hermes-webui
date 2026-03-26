#!/bin/bash
# ── Hermes Web UI Launcher ──────────────────────────────
# Usage: ./start.sh          (starts on port 5000)
#        ./start.sh 8080     (starts on custom port)

PORT="${1:-5000}"
HERMES_VENV="$HOME/.hermes/hermes-agent/venv"
APP_DIR="$HOME/hermes-web-ui"

echo "=========================================="
echo "  Hermes Agent Web UI"
echo "  http://127.0.0.1:$PORT"
echo "=========================================="
echo ""

cd "$APP_DIR"

# Check if already running on this port
if curl -s "http://127.0.0.1:$PORT/api/status" > /dev/null 2>&1; then
    echo "  Web UI is already running on port $PORT"
    echo "  Open http://127.0.0.1:$PORT in your browser"
    echo "=========================================="
    exit 0
fi

# Start the server
FLASK_PORT="$PORT" "$HERMES_VENV/bin/python" "$APP_DIR/app.py" &
SERVER_PID=$!

echo "  Server PID: $SERVER_PID"
echo "  Press Ctrl+C to stop"
echo "=========================================="

# Wait for the server process
wait $SERVER_PID 2>/dev/null
