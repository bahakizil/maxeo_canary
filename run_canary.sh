#!/bin/bash

# Maxeo Canary Test Runner
# E2E monitoring and health checks for Maxeo AI platform

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "🐤 Maxeo Canary Test"
echo "========================================"
echo "Started: $(date)"
echo

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
if [ ! -f "venv/.dependencies_installed" ] || [ "requirements.txt" -nt "venv/.dependencies_installed" ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    playwright install chromium
    touch venv/.dependencies_installed
fi

# Load environment variables if .env exists
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Run canary test
echo -e "${GREEN}Running canary tests...${NC}"
echo

python -m canary.canary_test

EXIT_CODE=$?

echo
echo "========================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ Canary test PASSED${NC}"
else
    echo -e "${RED}❌ Canary test FAILED (exit code: $EXIT_CODE)${NC}"
fi
echo "Finished: $(date)"
echo "========================================"

exit $EXIT_CODE
