#!/usr/bin/env bash
# Prune hand-made investment_research DB snapshots.
#
# These are NOT produced by the application or by the test suite (tests use an
# in-memory SQLite database and never touch this directory). They are manual
# "snapshot before a risky change" copies made during interactive server
# sessions. 47 of them had accumulated to 17GB by 2026-08-26, taking the disk
# to 83%, which is why this exists.
#
# Safety properties, each covered by a check below:
#   1. The live DB and its -wal/-shm are NEVER candidates (name-excluded).
#   2. Aborts entirely if the live DB is missing or implausibly small.
#   3. Always keeps the KEEP_NEWEST most recent snapshots regardless of age,
#      so this can never leave zero fallback.
#   4. Dry-run unless --apply is passed.
#
# Usage:  prune_db_backups.sh [--apply]
set -euo pipefail

DB_DIR="${DB_DIR:-/root/investment-research/database}"
LIVE_DB="investment_research.db"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
KEEP_NEWEST="${KEEP_NEWEST:-1}"
MIN_LIVE_BYTES="${MIN_LIVE_BYTES:-100000000}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

log() { echo "$*"; command -v logger >/dev/null && logger -t prune-db-backups "$*" || true; }

cd "$DB_DIR" || { log "ABORT: no such dir $DB_DIR"; exit 1; }

# Guard 2: refuse to delete any fallback while the live DB looks wrong.
live_sz=$(stat -c %s "$LIVE_DB" 2>/dev/null || echo 0)
if [ "$live_sz" -lt "$MIN_LIVE_BYTES" ]; then
  log "ABORT: live DB missing or too small (${live_sz}B < ${MIN_LIVE_BYTES}B) - not pruning"
  exit 1
fi

# Guard 1: candidates are every regular file here EXCEPT the live DB trio.
mapfile -t cands < <(find . -maxdepth 1 -type f \
  ! -name "$LIVE_DB" ! -name "${LIVE_DB}-wal" ! -name "${LIVE_DB}-shm" \
  -printf "%T@\t%f\n" | sort -rn | cut -f2-)

# Guard 3: protect the newest KEEP_NEWEST regardless of age.
del=()
idx=0
for f in "${cands[@]}"; do
  idx=$((idx+1))
  [ "$idx" -le "$KEEP_NEWEST" ] && continue
  if [ -n "$(find "./$f" -maxdepth 0 -mtime +"$RETENTION_DAYS" -print 2>/dev/null)" ]; then
    del+=("$f")
  fi
done

# Report the population scanned, not just the count acted on, so a run that
# does nothing is distinguishable from a run that scanned nothing.
freed=0
for f in "${del[@]:-}"; do [ -n "$f" ] && freed=$((freed + $(stat -c %s "$f"))); done
log "scanned dir=$DB_DIR snapshots=${#cands[@]} retention=${RETENTION_DAYS}d keep_newest=$KEEP_NEWEST prunable=${#del[@]} bytes=$freed apply=$APPLY"

if [ "${#del[@]}" -eq 0 ]; then
  log "nothing to prune"
  exit 0
fi

for f in "${del[@]}"; do
  if [ "$APPLY" -eq 1 ]; then rm -f -- "$f"; log "deleted $f"; else log "would delete $f"; fi
done

[ "$APPLY" -eq 1 ] && log "pruned ${#del[@]} snapshot(s), freed $((freed/1048576)) MiB; disk now $(df -h / | awk "NR==2{print \$5\" used, \"\$4\" free\"}")"
exit 0
