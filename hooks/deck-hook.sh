#!/bin/sh
# Claude Code hook: forwards the hook's JSON (stdin) to Deckpad so the
# phone shows "needs permission" / "finished" and vibrates.
# Never blocks Claude: short timeout, always exits 0.
# The token is handed to curl on a file descriptor, not as an argument,
# so it never shows up in the process list.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN="$(cat "$DIR/.deck_token" 2>/dev/null)" || exit 0
curl -s -m 2 -o /dev/null -X POST "http://127.0.0.1:${DECK_PORT:-8765}/api/hook" \
  -H "Content-Type: application/json" -H @/dev/fd/3 --data-binary @- 2>/dev/null 3<<HDR
X-Deck-Token: $TOKEN
HDR
exit 0
