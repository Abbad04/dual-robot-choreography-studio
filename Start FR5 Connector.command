#!/bin/zsh

set -u

CONNECTOR_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$CONNECTOR_DIR" || exit 1

echo ""
echo "FR5 Choreography Connector for macOS"
echo "===================================="
echo "Keep this window open while controlling the robot."
echo ""

ROBOT_IP="192.168.58.2"
printf "Robot IP [192.168.58.2]: "
read -r ENTERED_IP
if [[ -n "$ENTERED_IP" ]]; then
  ROBOT_IP="$ENTERED_IP"
fi

echo ""
echo "Physical movement requires a clear workspace and reachable emergency stop."
printf "Type YES to allow Play, Pause, and Stop to control the FR5: "
read -r MOTION_REPLY

CONNECTOR_BIN=""
MACHINE_ARCH="$(uname -m)"
if [[ "$MACHINE_ARCH" == "arm64" && -x "$CONNECTOR_DIR/bin/FR5-Connector-arm64" ]]; then
  CONNECTOR_BIN="$CONNECTOR_DIR/bin/FR5-Connector-arm64"
elif [[ "$MACHINE_ARCH" == "x86_64" && -x "$CONNECTOR_DIR/bin/FR5-Connector-x86_64" ]]; then
  CONNECTOR_BIN="$CONNECTOR_DIR/bin/FR5-Connector-x86_64"
elif command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python 3.10 or newer is required. Download it from https://www.python.org/downloads/macos/"
    echo ""
    read -r "?Press Return to close."
    exit 1
  fi
  CONNECTOR_BIN="python3"
else
  echo "This download is incomplete and Python 3 was not found."
  echo "Download the complete macOS connector from the web app and try again."
  echo ""
  read -r "?Press Return to close."
  exit 1
fi

ARGS=("--robot-ip" "$ROBOT_IP" "--results-dir" "runs/fr5-live")
if [[ "$MOTION_REPLY" == "YES" ]]; then
  ARGS+=("--allow-motion")
else
  echo "Starting read-only because YES was not entered."
fi

echo ""
if [[ "$CONNECTOR_BIN" == "python3" ]]; then
  python3 "$CONNECTOR_DIR/fr5_connector.py" "${ARGS[@]}"
else
  "$CONNECTOR_BIN" "${ARGS[@]}"
fi
EXIT_CODE=$?

echo ""
echo "FR5 Connector closed."
read -r "?Press Return to close this window."
exit "$EXIT_CODE"
