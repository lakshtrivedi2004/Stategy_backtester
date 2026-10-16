#!/usr/bin/env bash
# Run the Strategy Backtester.
#
#   ./run.sh          install what's missing, build the site, serve everything on http://localhost:8000
#   ./run.sh dev      same setup, then run the API and the Vite dev server with live reload (http://localhost:5173)
#   ./run.sh test     run the backend tests
#
# Environment: PORT (default 8000), HOST (default 127.0.0.1; use 0.0.0.0 to allow other machines).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="${1:-serve}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
VENV="$ROOT/venv"
PY="$VENV/bin/python"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

hash_of() { cat "$@" 2>/dev/null | sha1sum | cut -d' ' -f1; }

# ---------------------------------------------------------------- prerequisites
command -v python3 >/dev/null || fail "python3 is not installed (Python 3.11 or newer is needed)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 3.11 or newer is needed (found $(python3 --version 2>&1))."
command -v npm >/dev/null || fail "Node.js / npm is not installed (Node 20 or newer is needed)."

# ---------------------------------------------------------------- backend deps
if [ ! -x "$PY" ]; then
  say "Creating Python virtual environment in venv/"
  python3 -m venv "$VENV"
fi
REQ_HASH="$(hash_of backend/requirements.txt backend/requirements-dev.txt)"
if [ "$(cat "$VENV/.deps-hash" 2>/dev/null)" != "$REQ_HASH" ]; then
  say "Installing Python packages"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r backend/requirements-dev.txt
  echo "$REQ_HASH" > "$VENV/.deps-hash"
fi

if [ "$MODE" = "test" ]; then
  say "Running tests"
  cd backend && exec "$PY" -m pytest -q
fi

# ---------------------------------------------------------------- frontend deps
NPM_HASH="$(hash_of frontend/package.json frontend/package-lock.json)"
if [ ! -d frontend/node_modules ] || [ "$(cat frontend/node_modules/.deps-hash 2>/dev/null)" != "$NPM_HASH" ]; then
  say "Installing frontend packages"
  (cd frontend && npm install --no-audit --no-fund --loglevel=error)
  echo "$NPM_HASH" > frontend/node_modules/.deps-hash
fi

port_busy() {
  "$PY" - "$1" <<'EOF'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
EOF
}
port_busy "$PORT" && fail "Port $PORT is already in use. Stop the other program or run: PORT=8001 ./run.sh"

# ---------------------------------------------------------------- run
case "$MODE" in
  dev)
    port_busy 5173 && fail "Port 5173 (the dev site) is already in use."
    say "API on http://$HOST:$PORT · site with live reload on http://localhost:5173 (Ctrl-C stops both)"
    (cd backend && exec "$PY" -m uvicorn backtester.api:app --reload --host "$HOST" --port "$PORT") &
    API_PID=$!
    trap 'kill $API_PID 2>/dev/null; wait $API_PID 2>/dev/null' EXIT INT TERM
    cd frontend && BACKTESTER_API="http://127.0.0.1:$PORT" npm run dev
    ;;
  serve)
    # rebuild the site only when its sources changed since the last build
    SRC_HASH="$(find frontend/src frontend/index.html frontend/public frontend/vite.config.ts frontend/package.json \
                 -type f -print0 | sort -z | xargs -0 cat | sha1sum | cut -d' ' -f1)"
    if [ ! -f frontend/dist/index.html ] || [ "$(cat frontend/dist/.src-hash 2>/dev/null)" != "$SRC_HASH" ]; then
      say "Building the site"
      (cd frontend && npm run build --silent >/dev/null)
      echo "$SRC_HASH" > frontend/dist/.src-hash
    fi
    say "Strategy Backtester running on http://localhost:$PORT (Ctrl-C to stop)"
    cd backend && exec "$PY" -m uvicorn backtester.api:app --host "$HOST" --port "$PORT"
    ;;
  *)
    fail "Unknown mode '$MODE'. Use: ./run.sh [serve|dev|test]"
    ;;
esac
