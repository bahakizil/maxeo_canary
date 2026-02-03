# Maxeo Canary Test System

End-to-end monitoring and canary testing for Maxeo AI platform.

## Overview

This repository contains a comprehensive canary test system that monitors the full customer journey on the Maxeo AI platform, from landing page to workspace creation, category discovery, and snapshot analysis.

## What it Does

The canary test system:
- 🌐 **Browser Automation:** Opens maxeo.ai, navigates through the entire user flow
- 🔐 **Authentication:** Handles OTP-based login
- 🏢 **Workspace Creation:** Creates test workspace with AI-powered brand analysis
- 📊 **Database Verification:** Verifies all data is correctly stored in PostgreSQL
- 📈 **Category & Snapshot:** Monitors AI agent execution (category discovery, snapshot analysis)
- 💬 **Slack Alerts:** Sends detailed reports to Slack with metrics and screenshots
- 🧹 **Auto Cleanup:** Cleans up test data after execution

## Structure

```
maxeo_canary/
├── canary/
│   ├── canary_test.py          # Main orchestrator (41KB)
│   ├── alerting.py             # Slack/Sentry alerts (28KB)
│   ├── browser_automation.py   # Playwright automation (50KB)
│   ├── db_verification.py      # Database checks (26KB)
│   ├── cleanup.py              # Test cleanup (8KB)
│   ├── config.py               # Configuration (3.5KB)
│   ├── utils.py                # Utilities (3KB)
│   └── __init__.py             # Module init
├── run_canary.sh               # Runner script
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
└── README.md
```

## Setup

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### 2. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit .env with your settings
nano .env
```

Required environment variables:
- **Database:** `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, etc.
- **Canary Settings:** `CANARY_BASE_URL`, `CANARY_SLACK_WEBHOOK`
- **Auth:** `ADMIN_TOTP_SECRET`, `FERNET_ENCRYPTION_KEY`
- **AI:** `OPENROUTER_API_KEY`

### 3. Run Tests

```bash
# Run with runner script (recommended)
./run_canary.sh

# Or run directly
python -m canary.canary_test
```

## Deployment

This canary system is deployed on EC2 alongside the main Maxeo platform:
- **Location:** `/home/ubuntu/maxeo-canary/`
- **Purpose:** Continuous monitoring and health checks
- **Schedule:** Runs periodically via systemd timer

## Related Repositories

- [Maxeo Backend](https://github.com/Cool-Digital-Solutions/maxeo-backend-git) - Main FastAPI backend
- [Maxeo Frontend](https://github.com/Cool-Digital-Solutions/maxeo-frontend-git) - Next.js frontend
- [Maxeo Benchmark](https://github.com/bahakizil/maxeo_benchmark) - GEO Benchmark tool

## Configuration

Canary tests use the following environment variables (configured on EC2):
- Database connection settings
- AI API keys (OpenRouter, etc.)
- Service endpoints

**Note:** Never commit `.env` files or API keys to this repository.

## License

Proprietary - Maxeo AI / Cool Digital Solutions

## Authors

- Baha Kızıl
- Cool Digital Solutions Team
