#!/usr/bin/env bash
# Start the A2B API and vendor console.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating .venv…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f data/a2b.db ]; then
  echo "No database yet — seeding the Bluebonnet sample."
  ./.venv/bin/python scripts/seed_demo.py
fi

echo
echo "  console   http://127.0.0.1:8000/console/"
echo "  api docs  http://127.0.0.1:8000/docs"
echo "  mcp tools http://127.0.0.1:8000/mcp/tools"
echo
exec ./.venv/bin/uvicorn a2b.api.main:app --reload --port "${PORT:-8000}"
