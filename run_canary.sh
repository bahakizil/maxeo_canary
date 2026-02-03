#!/bin/bash
cd /home/ubuntu/maxeo-canary
source venv/bin/activate
export $(cat .env | xargs)
echo "======== CANARY TEST STARTED: $(date) ========"
python -m app.modules.canary.canary_test
echo "======== CANARY TEST ENDED: $(date) ========"
