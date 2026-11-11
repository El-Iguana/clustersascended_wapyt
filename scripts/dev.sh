#!/usr/bin/env bash
# Run the dev server in the background: scripts/dev.sh start|stop|restart|log
# Tracks its own PID in data/dev.pid. Never stop it by matching
# "python service.py": other wapyt apps' containers run the same command line.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
PIDFILE=data/dev.pid
LOG=data/dev.log
PORT="${PORT:-8098}"
mkdir -p data

stop() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
    for _ in $(seq 50); do kill -0 "$(cat "$PIDFILE")" 2>/dev/null || break; sleep 0.1; done
  fi
  rm -f "$PIDFILE"
}

start() {
  setsid uv run python service.py >"$LOG" 2>&1 < /dev/null &
  echo $! >"$PIDFILE"
  for _ in $(seq 150); do
    curl -fs -o /dev/null "http://127.0.0.1:${PORT}/clusters_ascended/login?manual=1" && {
      echo "up: http://127.0.0.1:${PORT}/clusters_ascended/"; return; }
    sleep 0.2
  done
  echo "did not come up; see $LOG" >&2; tail -20 "$LOG" >&2; exit 1
}

case "${1:-start}" in
  start) stop; start ;;
  stop) stop ;;
  restart) stop; start ;;
  log) tail -f "$LOG" ;;
  *) echo "usage: $0 start|stop|restart|log" >&2; exit 2 ;;
esac
