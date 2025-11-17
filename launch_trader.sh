#!/bin/sh
# Launch the Griffin trader with sensible defaults. Additional CLI args are forwarded.

set -e

if [ -d ".venv" ]; then
  . ./.venv/bin/activate
fi

python -m alpaca_trader.cli trade "$@"
