#!/bin/bash

# BioRxiv Research Tool - Launch Script
# Handles environment setup and application launch

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   BioRxiv Research Tool${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"

# Check Python version
echo -e "\n${BLUE}Checking Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python3 not found${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓ Python ${PYTHON_VERSION}${NC}"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "\n${BLUE}Creating virtual environment...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi

# Activate virtual environment
echo -e "\n${BLUE}Activating virtual environment...${NC}"
source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"

# Install/update dependencies
echo -e "\n${BLUE}Checking dependencies...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${RED}Error: requirements.txt not found${NC}"
    exit 1
fi

# Check for key_terms.json
echo -e "\n${BLUE}Checking configuration...${NC}"
if [ -f "key_terms.json" ]; then
    echo -e "${GREEN}✓ key_terms.json found${NC}"
else
    echo -e "${YELLOW}Warning: key_terms.json not found${NC}"
fi

# Check if Ollama is running (optional, but helpful)
echo -e "\n${BLUE}Checking Ollama...${NC}"
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Ollama is running${NC}"

    # Check if Qwen model is available
    if curl -s http://localhost:11434/api/tags | grep -q "qwen"; then
        echo -e "${GREEN}✓ Qwen model available${NC}"
    else
        echo -e "${YELLOW}⚠ Qwen model not found. Run: ollama pull qwen:7b${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Ollama not running. Summarization will fail.${NC}"
    echo -e "${YELLOW}  Start it with: ollama serve${NC}"
fi

# Create preprints directory if needed in home directory
echo -e "\n${BLUE}Checking data directory...${NC}"
mkdir -p ~/preprints/PDFs ~/preprints/summaries
echo -e "${GREEN}✓ ~/preprints directory ready${NC}"

# Run tests if requested
if [ "$1" = "test" ]; then
    echo -e "\n${BLUE}Running component tests...${NC}"
    python3 test_components.py
    exit $?
fi

# Show usage info
echo -e "\n${BLUE}Available commands:${NC}"
echo -e "  ${GREEN}./run.sh${NC}              Launch GUI"
echo -e "  ${GREEN}./run.sh test${NC}         Run component tests"
echo -e "  ${GREEN}./run.sh search${NC}       Run search agent (all enabled)"
echo -e "  ${GREEN}./run.sh summarize${NC}    Run summarization agent"
echo -e "  ${GREEN}./run.sh help${NC}         Show this help"

# Execute command based on argument
case "$1" in
    test)
        echo -e "\n${BLUE}Running tests...${NC}"
        python3 test_components.py
        ;;
    search)
        echo -e "\n${BLUE}Running search agent...${NC}"
        python3 agents/search_agent.py --all
        ;;
    summarize)
        echo -e "\n${BLUE}Running summarization agent...${NC}"
        python3 agents/summarization_agent.py
        ;;
    help)
        echo -e "\n${BLUE}Usage:${NC}"
        echo -e "  ./run.sh              Launch GUI (default)"
        echo -e "  ./run.sh test         Run component tests"
        echo -e "  ./run.sh search       Run search agent"
        echo -e "  ./run.sh summarize    Run summarization agent"
        ;;
    "")
        # No argument, launch GUI
        echo -e "\n${BLUE}Launching GUI...${NC}"
        echo -e "${YELLOW}(Close the window to exit)${NC}\n"
        python3 gui.py
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        echo -e "Run ${GREEN}./run.sh help${NC} for available commands"
        exit 1
        ;;
esac
