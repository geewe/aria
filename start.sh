#!/bin/bash
cd "$(dirname "$0")"
export HASS_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI3ZmIyNzJlYmM3YTk0YzhmOGJhOGRhYjYwMGY3MTcwNyIsImlhdCI6MTc4MDE5NDIwMywiZXhwIjoyMDk1NTU0MjAzfQ.iAUI64da8TmiHX9xEHCuaXPJz-4zUqyItG3Aol_sl-I"
export HASS_URL="http://192.168.2.45:8123"
exec ./venv/bin/python3 -m uvicorn butler.server:app --host 0.0.0.0 --port "${PORT:-8650}" "$@"
