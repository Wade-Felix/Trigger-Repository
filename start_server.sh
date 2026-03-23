#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000 >> logs/server.log 2>&1
