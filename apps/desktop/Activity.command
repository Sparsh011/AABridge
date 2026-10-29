#!/bin/zsh -f
set -eu
cd -- "${0:A:h}"
for aabridge_activity_python in "$HOME/Library/Application Support/AABridgeInstall/venv/bin/python" /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 python3.11 python3.12 python3.13 python3.14 python3; do
  if command -v "$aabridge_activity_python" >/dev/null 2>&1 && "$aabridge_activity_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    exec "$aabridge_activity_python" ./activity_backend.py --bundle "$PWD/SetupPayload" "$@"
  fi
done
print '{"error":{"message":"Python 3.11+ is required to read bridge activity. Receiver setup can prepare Python using an existing Homebrew installation."}}'
exit 1
