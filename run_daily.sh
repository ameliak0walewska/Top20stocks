#!/bin/bash
# Daily dry-run report for the Top20stocks pipeline.
# Re-sizes positions against whatever top20.csv currently exists (it only
# actually changes when Discovery/Artemis refreshes it weekly) and prints
# the planned trades — nothing is ever submitted to Alpaca by this script.

set -uo pipefail

PROJECT_DIR="$HOME/PycharmProjects/Top20stocks"
PY="$HOME/anaconda3/bin/python3"
LOG_DIR="$PROJECT_DIR/logs/daily"

cd "$PROJECT_DIR" || { echo "Could not cd to $PROJECT_DIR"; exit 1; }
mkdir -p "$LOG_DIR"

TS=$(date +"%Y%m%dT%H%M%S")
LOG_FILE="$LOG_DIR/run_$TS.log"

{
  echo "=== Daily dry-run started: $(date) ==="
  echo ""
  echo "--- position sizing ---"
  "$PY" position_sizing.py top20.csv --output target_positions.csv --force
  SIZING_STATUS=$?
  echo ""
  if [ $SIZING_STATUS -eq 0 ]; then
    echo "--- dry-run trade plan ---"
    "$PY" trade_from_csv.py target_positions.csv --dry-run
    echo ""
    echo "--- dashboard ---"
    "$PY" build_dashboard.py
    echo ""
    echo "--- publish dashboard to GitHub Pages ---"
    git add docs/index.html docs/.nojekyll 2>&1
    if git diff --cached --quiet; then
      echo "No dashboard changes to publish."
    else
      git commit -m "Nightly dashboard update $(date +%Y-%m-%d)" 2>&1
      if git push origin main 2>&1; then
        echo "Pushed. Live at your GitHub Pages URL (Settings -> Pages) within a minute or two."
      else
        echo "git push failed - dashboard.html was still generated locally, just not published. Check network/auth."
      fi
    fi
  else
    echo "position_sizing.py failed (exit $SIZING_STATUS) - skipping trade plan and dashboard."
  fi
  echo ""
  echo "=== Daily dry-run finished: $(date) ==="
} > "$LOG_FILE" 2>&1

# Keep a pointer to the most recent run for convenience
cp "$LOG_FILE" "$LOG_DIR/latest.log"

echo "Done. Log written to $LOG_FILE"
