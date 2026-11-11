#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$PWD/.data/cache/pip}"
export npm_config_cache="${npm_config_cache:-$PWD/.data/cache/npm}"
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
(cd frontend && npm ci && npm run build)
