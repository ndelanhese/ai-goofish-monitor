# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Playwright + AI-powered intelligent monitor bot for Goofish (Xianyu). FastAPI backend + Vue 3 frontend, supporting multi-task concurrent monitoring, multimodal AI product analysis, and multi-channel notification delivery.

## Core Architecture

```
API Layer (src/api/routes/)
    ↓
Service Layer (src/services/)
    ↓
Domain Layer (src/domain/)
    ↓
Infrastructure Layer (src/infrastructure/)
```

Key entry points:
- `src/app.py` - FastAPI application main entry
- `spider_v2.py` - Scraper CLI entry
- `src/scraper.py` - Playwright scraper core logic

Service layer:
- `TaskService` - Task CRUD
- `ProcessService` - Scraper subprocess management
- `SchedulerService` - APScheduler scheduled dispatch
- `AIAnalysisService` - Multimodal AI analysis
- `NotificationService` - Multi-channel notifications (ntfy/Bark/WeChat Work/Telegram/Webhook)

Frontend (`web-ui/`): Vue 3 + Vite + shadcn-vue + Tailwind CSS

## Development Commands

```bash
# Backend development
python -m src.app
# or
uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload

# Frontend development
cd web-ui && npm install && npm run dev

# Frontend build
cd web-ui && npm run build

# One-click local start (build frontend + start backend)
bash start.sh

# Docker deployment
docker compose up --build -d
```

## Scraper Commands

```bash
python spider_v2.py                          # Run all enabled tasks
python spider_v2.py --task-name "MacBook"    # Run a specific task
python spider_v2.py --debug-limit 3          # Debug mode, limit product count
python spider_v2.py --config custom.json     # Custom config file
```

## Testing

```bash
pytest                              # Run all tests
pytest --cov=src                    # Coverage report
pytest tests/unit/test_utils.py    # Run a single test file
pytest tests/unit/test_utils.py::test_safe_get  # Run a single test function
```

Test conventions: files `tests/**/test_*.py`, functions `test_*`

## Configuration

Environment variables (`.env`):
- AI model: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL_NAME`
- Notifications: `NTFY_TOPIC_URL`, `BARK_URL`, `WX_BOT_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Scraper: `RUN_HEADLESS`, `LOGIN_IS_EDGE`
- Web authentication: `WEB_USERNAME`, `WEB_PASSWORD`
- Port: `SERVER_PORT`

Task configuration (`config.json`): defines monitoring tasks (keywords, price range, cron expression, AI prompt files, etc.)

## Data Flow

1. Web UI / config.json creates tasks
2. SchedulerService triggers on cron or manual start
3. ProcessService launches spider_v2.py subprocess
4. scraper.py uses Playwright to scrape products
5. AIAnalysisService calls multimodal model for analysis
6. NotificationService pushes matching products
7. Results stored: `jsonl/` (data), `images/` (images), `logs/` (logs)

## Notes

- The AI model must support image input (multimodal)
- Docker deployment requires manually updating login state (`state.json`) via Web UI
- When encountering a slider CAPTCHA, set `RUN_HEADLESS=false` to handle it manually
- Always change the default Web authentication password in production
