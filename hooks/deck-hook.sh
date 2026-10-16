#!/bin/sh
# Claude Code hook: forwards the hook's JSON (stdin) to Deckpad so the
# phone shows "needs permission" / "finished" and vibrates.
# Never blocks Claude: short timeout, always exits 0.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN="$(cat "$DIR/.deck_token" 2>/dev/null)" || exit 0
curl -s -m 2 -o /dev/null -X POST "http://127.0.0.1:${DECK_PORT:-8765}/api/hook" \
  -H "Content-Type: application/json" -H "X-Deck-Token: $TOKEN" --data-binary @- 2>/dev/null
exit 0
