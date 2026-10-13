#!/bin/zsh
# Manage nullmontage as a macOS launchd user agent (starts at login, restarts on crash).
# usage: scripts/service.sh install | uninstall | start | stop | restart | status | logs
set -e
LABEL="com.nullmontage.server"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$DIR/data/server.log"
UID_="$(id -u)"

install() {
  mkdir -p "$HOME/Library/LaunchAgents" "$DIR/data"
  [ -x "$DIR/.venv/bin/nullmontage" ] || (cd "$DIR" && uv sync --all-groups)
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/.venv/bin/nullmontage</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PORT</key><string>${PORT:-8765}</string>
    <key>NULLMONTAGE_HOST</key><string>${NULLMONTAGE_HOST:-127.0.0.1}</string>
    <key>PATH</key><string>$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PL
  # Anthropic key for the director's notes, if present in the shell, is passed through to the service.
  if [ -n "$ANTHROPIC_API_KEY" ]; then
    /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:ANTHROPIC_API_KEY string $ANTHROPIC_API_KEY" "$PLIST"
  fi
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_" "$PLIST"
  echo "installed + started: http://localhost:${PORT:-8765}"
}
uninstall() { launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true; rm -f "$PLIST"; echo "removed"; }
start()   { launchctl kickstart "gui/$UID_/$LABEL"; echo started; }
stop()    { launchctl bootout "gui/$UID_/$LABEL"; echo stopped; }
restart() { launchctl kickstart -k "gui/$UID_/$LABEL"; echo restarted; }
status()  { launchctl print "gui/$UID_/$LABEL" 2>/dev/null | grep -E "state|pid|last exit" || echo "not installed"; curl -s -o /dev/null -w "http://localhost:${PORT:-8765} → HTTP %{http_code}\n" "http://localhost:${PORT:-8765}/" || true; }
logs()    { tail -n 50 -f "$LOG"; }

case "${1:-status}" in
  install|uninstall|start|stop|restart|status|logs) "$1" ;;
  *) echo "usage: $0 install|uninstall|start|stop|restart|status|logs"; exit 1 ;;
esac
