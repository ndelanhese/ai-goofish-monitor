# Testing Guide

This project uses pytest as its test framework. The following guide explains how to run the tests.

## Installing Dependencies

Before running the tests, make sure all development dependencies are installed:

```bash
pip install -r requirements.txt
```

## Running Tests

### Run all tests

```bash
pytest
```

### Run a specific test file

```bash
pytest tests/integration/test_api_tasks.py
```

### Run a specific test function

```bash
pytest tests/unit/test_utils.py::test_safe_get_nested_and_default
```

### Generate a coverage report

```bash
coverage run -m pytest
coverage report
coverage html  # generate an HTML report
```

## Test File Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures (API/CLI/sample data)
├── fixtures/                # Realistic sample data (search results/user info/ratings/task config)
│   ├── config.sample.json
│   ├── ratings.json
│   ├── search_results.json
│   ├── state.sample.json
│   ├── user_head.json
│   └── user_items.json
├── integration/             # Critical-path integration tests (API/CLI/parsers)
│   ├── test_api_tasks.py
│   ├── test_cli_spider.py
│   └── test_pipeline_parse.py
└── unit/                    # Core pure-function unit tests
    ├── test_domain_task.py
    └── test_utils.py
```

## Writing New Tests

1. Add new tests under `tests/integration/` or `tests/unit/`
2. File names must start with `test_`; function names must start with `test_`
3. Tests are executed synchronously (pytest-asyncio is not required)
4. Mock all external dependencies (Playwright / AI / notifications / network)
5. Use sample data from `tests/fixtures/` to avoid relying on real network calls

## Notes

1. The goal is offline-runnable and stably reproducible tests
2. Integration tests should prioritise covering real execution paths (API, CLI, parsers)
3. When new real-world sample scenarios are needed, add them to `tests/fixtures/`

## Live Smoke Tests

- Directory: `tests/live/`
- Disabled by default; executed only when `RUN_LIVE_TESTS=1` is explicitly set
- Recommended command:

```bash
RUN_LIVE_TESTS=1 \
LIVE_TEST_ACCOUNT_STATE_FILE=/absolute/path/to/account.json \
LIVE_TEST_KEYWORD="MacBook Pro M2" \
pytest tests/live -m live -v
```

- One-click script:

```bash
./run_live_smoke.sh
./run_live_smoke.sh --without-generation
```

- Optional environment variables:
  - `LIVE_TEST_TASK_NAME`
  - `LIVE_EXPECT_MIN_ITEMS` (default `1`)
  - `LIVE_TEST_DEBUG_LIMIT` (default `1`; scrape/analyse only the first N new products)
  - `LIVE_TIMEOUT_SECONDS` (default `180`)
  - `LIVE_ENABLE_TASK_GENERATION` (script default `1`; set to `0` or use `--without-generation` to disable the real AI task-generation slow test)
- The live suite starts a real `uvicorn` instance in a temporary working directory and clears all notification-related environment variables to avoid polluting the repository root or sending messages to real notification channels.
