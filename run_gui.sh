#!/bin/bash

# BioRx Research Tool - Simple GUI Launch Script

# Get the script's directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Ensure preprints directory exists in home directory
mkdir -p ~/preprints/PDFs ~/preprints/summaries

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment 'venv' not found. Please run 'python3 -m venv venv && pip install -r requirements.txt' first."
    exit 1
fi

# Run the GUI
echo "Launching BioRx GUI..."
python3 gui.py
