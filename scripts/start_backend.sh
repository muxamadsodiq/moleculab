#!/usr/bin/env sh
# Start MOLECULAB (API + web UI) on localhost. Works on Linux and in Git Bash on Windows.
#   ./scripts/start_backend.sh [port]      default port 8765; add --reload via MOLECULAB_RELOAD=1
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PORT=${1:-8765}
HOST=127.0.0.1

if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
    PY="$ROOT/.venv/Scripts/python.exe"
else
    echo "error: no virtual environment at $ROOT/.venv - follow LAPTOP_SETUP.md first" >&2
    exit 1
fi

# Refuse to start a second copy on the same port.
if "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('$HOST', $PORT)) == 0 else 1)"; then
    echo "error: something is already listening on http://$HOST:$PORT (MOLECULAB already running?)" >&2
    exit 1
fi

[ -f "$ROOT/.env" ] || echo "note: no .env file; using defaults (SQLite at $ROOT/moleculab.db, no LLM)"

RELOAD=""
[ "${MOLECULAB_RELOAD:-0}" = "1" ] && RELOAD="--reload"

cd "$ROOT"
echo "MOLECULAB -> http://$HOST:$PORT  (API docs: http://$HOST:$PORT/docs)   Ctrl+C to stop"
# .env is read by app/config.py itself; its values are never echoed here.
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD
