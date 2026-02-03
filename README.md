# Maxeo Canary Test System

End-to-end monitoring and canary testing for Maxeo AI platform.

## Overview

This repository contains canary test scripts for monitoring the health and functionality of the Maxeo AI platform in production.

## Structure

```
maxeo_canary/
├── canary/
│   └── canary_test.py      # Main canary test module
├── run_canary.sh            # Canary test runner script
└── README.md
```

## Canary Test Module

The `canary_test.py` module provides E2E testing capabilities for:
- Workspace creation
- AI agent operations
- API endpoint health checks
- Database operations
- System integration tests

## Usage

### Run Canary Tests

```bash
# Run all canary tests
./run_canary.sh

# Run specific test
python canary/canary_test.py
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
