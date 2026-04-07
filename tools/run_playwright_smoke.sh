#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCREENSHOT_DIR="${1:-/tmp/hermes-pw-smoke}"
URL="${2:-http://127.0.0.1:5057/}"
shift $(( $# > 0 ? 1 : 0 ))
shift $(( $# > 0 ? 1 : 0 ))

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi

if [[ -z "${HERMES_WEBUI_TOKEN:-}" ]]; then
  echo "HERMES_WEBUI_TOKEN is not set. Add it to $ROOT_DIR/.env or export it before running." >&2
  exit 1
fi

export TMPDIR=/tmp
export TMP=/tmp
export TEMP=/tmp
export PLAYWRIGHT_BROWSERS_PATH=/tmp/pw-browsers

exec "$HOME/.hermes/.venv/bin/python" \
  "$ROOT_DIR/tools/playwright_smoke.py" \
  --url "$URL" \
  --token "$HERMES_WEBUI_TOKEN" \
  --screenshot-dir "$SCREENSHOT_DIR" \
  "$@"
