#!/bin/bash
SESSION="omarion"

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -c "$HOME/projects/Omarion" \
  "bash -c '. $HOME/.local/bin/env && claude --dangerously-skip-permissions'"

(
  while ! tmux capture-pane -t "$SESSION" -p 2>/dev/null | grep -q "bypass permissions"; do
    sleep 0.5
  done
  sleep 1
  tmux send-keys -t "$SESSION" "/remote-control" Enter
) &

echo "omarion → tmux attach -t $SESSION"
