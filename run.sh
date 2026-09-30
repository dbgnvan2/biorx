#!/bin/bash
# BioRx — helper for the headless tools and the test suite.
#
# The web app is the main surface (see README: uvicorn web.app:app). The
# legacy bioRxiv-only search agent and its cluster file were removed (review S4,
# 2026-09-28): saved searches are filters.json, run by agents/monitor.py.

set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "No venv/ here. Create it: python3 -m venv venv && venv/bin/pip install -r requirements-web.txt"
    exit 1
fi
PY=venv/bin/python

usage() {
    echo "Usage:"
    echo "  ./run.sh search      Run every enabled saved filter (agents/monitor.py --all)"
    echo "  ./run.sh summarize   Summarize stored papers that have no summary yet"
    echo "  ./run.sh test        Run the test suite"
}

case "$1" in
    search)    shift; exec "$PY" agents/monitor.py --all "$@" ;;
    summarize) shift; exec "$PY" agents/summarization_agent.py "$@" ;;
    test)      shift; exec "$PY" -m pytest tests/ "$@" ;;
    ""|help)   usage ;;
    *)         echo "Unknown command: $1"; usage; exit 1 ;;
esac
