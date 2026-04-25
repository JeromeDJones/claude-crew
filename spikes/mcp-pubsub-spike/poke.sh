#!/usr/bin/env bash
# Trigger an inbox update from outside the Claude session.
# Usage: ./poke.sh "your message here"
set -euo pipefail
msg="${1:-hello from outside $(date +%H:%M:%S)}"
echo "$msg" > /tmp/spike-poke
echo "poked: $msg"
