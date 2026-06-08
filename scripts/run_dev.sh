#!/usr/bin/env bash
# Run the backend (FastAPI/uvicorn) and the dashboard (Streamlit) together,
# both on localhost. Ctrl-C stops both.
set -euo pipefail

cd "$(dirname "$0")/.."

# Activate the venv we created during scaffolding.
source .venv/bin/activate

# 1. Backend on :8000
uvicorn src.api:app --host 127.0.0.1 --port 8000 --log-level info &
backend_pid=$!
trap "echo 'stopping...'; kill $backend_pid 2>/dev/null || true" EXIT

# Give uvicorn a moment to bind.
sleep 2

# 2. Dashboard on :8501
streamlit run src/dashboard.py --server.address 127.0.0.1 --server.port 8501 \
    --server.headless true --browser.gatherUsageStats false
