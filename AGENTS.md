# Repository Guidelines

## Project Structure and Module Organization
- Backend is in `src/`, entry point `src/app.py`, API routes in `src/api/routes/`, service layer in `src/services/`, domain models in `src/domain/`, infrastructure in `src/infrastructure/`.
- Frontend is in `web-ui/` (Vue 3 + Vite), views in `web-ui/src/views/`, components in `web-ui/src/components/`, build artifacts are copied to the root `dist/`.
- Tests are in `tests/`, following the naming convention `test_*.py` or `tests/*/test_*.py`.
- Runtime data and resources: `prompts/`, `jsonl/`, `logs/`, `images/`, `static/`, `state/`; config files `config.json` and `.env` are in the repository root.

## Build, Test, and Local Development
- Backend development: `python -m src.app` or `uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload`.
- Scraper tasks: `python spider_v2.py --task-name "MacBook Air M1" --debug-limit 3` (use `--config` to specify a custom config).
- Frontend development: `cd web-ui && npm install && npm run dev`; build: `cd web-ui && npm run build` (artifacts copied to root `dist/`).
- One-click local start: `bash start.sh` (automatically installs dependencies, builds frontend, and starts backend).
- Docker: `docker compose up --build -d`, view logs with `docker compose logs -f app`, stop with `docker compose down`.

## Coding Style and Naming Conventions
- Maintain layering: API → services → domain → infrastructure; avoid cross-layer coupling and keep modules lean.
- Python test functions are named `test_*`; files and paths follow the test directory conventions above.
- Use descriptive, task-oriented naming (e.g., scraper task names, config keys) that corresponds to business meaning.

## Architecture and Runtime
- The backend uses FastAPI to serve API endpoints and static assets; the scraper and AI inference run in separate task processes that communicate with the main service via HTTP/Web UI.
- Task runs write results to `jsonl/`, store runtime logs in `logs/`, and download images to `images/`; the frontend monitoring page depends on this data.
- Default listening port is 8000; once the frontend is built, static files can be served directly by the backend or Docker image.

## Testing Guide
- Test framework: `pytest` (synchronous tests by default; `pytest-asyncio` is not required).
- Run all tests: `pytest`; coverage: `pytest --cov=src` or `coverage run -m pytest`; targeted test: `pytest tests/test_utils.py::test_safe_get`.
- Prioritize covering exception branches and retry logic in core services and the scraper pipeline to prevent regressions.
- Run relevant tests before a PR and add focused test cases for new logic.

## Commit and PR Guidelines
- Commits follow Conventional Commits style: `feat(...)`, `fix(...)`, `refactor(...)`, `chore(...)`, `docs(...)`, etc.
- PRs should describe the scope of change and affected modules; provide screenshots for UI changes in `web-ui/`; reference related issues; mention any configuration or migration steps.

## Security and Configuration Notes
- Copy `.env.example` to `.env` and set the required fields `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL_NAME`, etc.
- Do not commit real credentials or cookies (e.g., `state.json`); Playwright requires a local browser, and the Docker image already includes Chromium.
- Default Web credentials are `admin/admin123`; change them in production. It is recommended to enable HTTPS and restrict access sources.
