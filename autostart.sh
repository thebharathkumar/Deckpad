#!/bin/sh
# Start Deckpad automatically when you log in to the Mac.
#   ./autostart.sh on        install + start (USB cable only — nothing reachable over Wi-Fi)
#   ./autostart.sh on wifi   same, but the phone can also connect over Wi-Fi
#   ./autostart.sh off       stop + remove
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="app.deckpad.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3.12 || command -v python3)"
USB_ONLY=1; [ "$2" = "wifi" ] && USB_ONLY=0

case "$1" in
  on)
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/server.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>DECK_USB_ONLY</key><string>$USB_ONLY</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/deck.log</string>
  <key>StandardErrorPath</key><string>$DIR/deck.log</string>
</dict></plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Deckpad will now run at login. Log: $DIR/deck.log"
    ;;
  off)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Autostart removed."
    ;;
  *) echo "usage: $0 on|off"; exit 1 ;;
esac
