#!/usr/bin/env bash
#
# Launch the photo-frame kiosk browser by hand — no systemd — for testing.
#
# Starts the Flask app (app.py) if it isn't already running, then opens Chromium
# fullscreen with the same GPU/kiosk flags as the service, auto-detecting Wayland
# vs X11. When you quit the browser, the app it started is shut down too.
#
# Run it from inside your desktop session (so Chromium can reach the compositor):
#
#   ./deploy/run-kiosk.sh                       # opens http://localhost:5000
#   ./deploy/run-kiosk.sh http://localhost:5000/remote
#
# Exit the kiosk with Alt+F4 (closes the window) or Ctrl+C in this terminal.
# F11 does nothing — Chromium's --kiosk mode disables it on purpose.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

URL="${1:-http://localhost:5000}"
APP_HOST="localhost"
APP_PORT="5000"                     # app.py binds 0.0.0.0:5000
APP_LOG="/tmp/photo-frame-app.log"

# PIDs of helpers we start, so cleanup only touches what we own.
APP_PID=""
UNCLUTTER_PID=""

cleanup() {
  if [ -n "$APP_PID" ]; then
    echo "Stopping app.py..."
    # Negative PID = the app's whole process group, so Flask's debug-reloader
    # child (which actually holds the port) is stopped too.
    kill -TERM "-$APP_PID" 2>/dev/null || kill -TERM "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  [ -n "$UNCLUTTER_PID" ] && kill "$UNCLUTTER_PID" 2>/dev/null || true
}
trap cleanup EXIT

app_is_up() {
  # Succeeds if something is already answering on APP_HOST:APP_PORT.
  (echo > "/dev/tcp/$APP_HOST/$APP_PORT") >/dev/null 2>&1
}

# --- Start the Flask app if it isn't already running ---------------------------
if app_is_up; then
  echo "App already answering on $APP_HOST:$APP_PORT — leaving it as-is."
else
  PYTHON="$REPO_ROOT/venv/bin/python"
  [ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
  echo "Starting app.py ($PYTHON), logging to $APP_LOG ..."
  # `set -m` puts the backgrounded job in its own process group so cleanup can
  # signal the group (parent + reloader child) with a single negative-PID kill.
  set -m
  ( cd "$REPO_ROOT" && exec "$PYTHON" app.py ) >"$APP_LOG" 2>&1 &
  APP_PID=$!
  set +m

  # Wait for it to come up (up to ~15s), bailing out early if it dies.
  for _ in $(seq 1 30); do
    app_is_up && break
    if ! kill -0 "$APP_PID" 2>/dev/null; then
      echo "app.py exited during startup — see $APP_LOG" >&2
      exit 1
    fi
    sleep 0.5
  done
  if ! app_is_up; then
    echo "app.py did not start listening on $APP_HOST:$APP_PORT — see $APP_LOG" >&2
    exit 1
  fi
  echo "App is up."
fi

# --- Launch Chromium -----------------------------------------------------------
# The package is 'chromium' on current Raspberry Pi OS and 'chromium-browser'
# on older releases — accept either.
CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM" ]; then
  echo "Chromium not found (tried 'chromium' and 'chromium-browser')." >&2
  echo "Install it with: sudo apt install chromium" >&2
  exit 1
fi

flags=(
  --kiosk
  --noerrdialogs
  --disable-infobars
  --disable-session-crashed-bubble
  --disable-component-update
  # GPU acceleration — see USAGE.md; verify at chrome://gpu
  --enable-gpu-rasterization
  --ignore-gpu-blocklist
  --enable-zero-copy
  --use-gl=egl
)

# When launched over SSH (or any non-graphical shell) the session env vars are
# unset, so probe for a running compositor's socket and adopt it. This lets
# `ssh pi ./deploy/run-kiosk.sh` reach the desktop already running on the seat.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
  runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  for sock in "$runtime_dir"/wayland-[0-9]*; do
    [ -S "$sock" ] || continue
    export XDG_RUNTIME_DIR="$runtime_dir"
    export WAYLAND_DISPLAY="${sock##*/}"
    echo "No graphical session in env; adopting compositor socket $WAYLAND_DISPLAY."
    break
  done
fi

if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  echo "Session: Wayland"
  flags+=(--ozone-platform=wayland --enable-features=UseOzonePlatform)
  # Note: unclutter is X11-only; hiding the cursor on Wayland is compositor-specific.
else
  echo "Session: X11"
  # Hide the mouse cursor after a brief idle (X11 only).
  if command -v unclutter >/dev/null; then
    unclutter -idle 0.1 -root &
    UNCLUTTER_PID=$!
  fi
fi

echo "Launching Chromium — exit with Alt+F4 or Ctrl+C."
# Foreground, so quitting the browser returns here and the EXIT trap cleans up.
"$CHROMIUM" "${flags[@]}" "$URL" || true
