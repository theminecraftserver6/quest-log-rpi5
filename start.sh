#!/bin/bash
# start.sh — Launch the Beta Quest server
# Run from the questlog/ directory: bash start.sh

PORT=${1:-8080}
DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "  ◈  Starting QuestLog Server on port $PORT..."
echo ""

cd "$DIR"
python3 server.py --port "$PORT"
