#!/bin/sh
# cron wrapper: loads keys, runs the requested pass, logs. Usage: ./run.sh probe | truth
set -e
cd "$(dirname "$0")"
[ -f "$HOME/.provider-probe.env" ] && . "$HOME/.provider-probe.env"
case "$1" in
  probe) python3 roll.py; exec python3 probe.py flights.csv --from-today ;;
  truth) exec python3 probe.py flights.csv --missing-truth ;;
  score) exec python3 score.py --csv ;;
  *) echo "usage: $0 probe|truth|score" >&2; exit 1 ;;
esac
