#!/bin/bash
# Record the Artel demo using two real Claude Code sessions.
# Records nova and orion separately, then combines into a split-pane cast.
# Requires: agg, and claude CLI in PATH.
#
# Usage: bash scripts/record_demo.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GIF="$SCRIPT_DIR/../docs/demo.gif"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python3"

# Record nova (top pane)
python3 "$SCRIPT_DIR/demo_record.py" nova /tmp/artel-nova.cast

# Record orion (bottom pane)
python3 "$SCRIPT_DIR/demo_record.py" orion /tmp/artel-orion.cast

# Combine into a split-pane cast (uses pyte from the project venv)
echo "Combining into split-pane cast..."
"$VENV_PYTHON" "$SCRIPT_DIR/demo_combine.py" \
  /tmp/artel-nova.cast \
  /tmp/artel-orion.cast \
  /tmp/artel-demo.cast

agg /tmp/artel-demo.cast "$GIF" \
  --font-size 14 \
  --theme monokai \
  --speed 3 \
  2>&1 | tail -1

ls -lh "$GIF"
echo "done → $GIF"
