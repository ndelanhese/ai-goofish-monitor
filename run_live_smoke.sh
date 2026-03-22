#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_CMD="${PYTHON_CMD:-python3}"
MARK_EXPRESSION=""
DRY_RUN=false
WITH_GENERATION=true
PYTEST_ARGS=()
TASK_CREATE_TEST="tests/integration/test_api_tasks.py::test_create_list_update_delete_task"
TEST_TARGETS=(
    "$TASK_CREATE_TEST"
    "tests/live"
)

usage() {
    cat <<'EOF'
Usage:
  ./run_live_smoke.sh [options] [-- extra pytest arguments]

Options:
  --keyword <keyword>          Override LIVE_TEST_KEYWORD
  --account-file <path>        Override LIVE_TEST_ACCOUNT_STATE_FILE
  --task-name <name>           Override LIVE_TEST_TASK_NAME
  --timeout <seconds>          Override LIVE_TIMEOUT_SECONDS
  --min-items <count>          Override LIVE_EXPECT_MIN_ITEMS
  --debug-limit <count>        Override LIVE_TEST_DEBUG_LIMIT (default 1; analyse only the first N new products)
  --with-generation            Explicitly enable live_slow (enabled by default)
  --without-generation         Disable live_slow; run only the main smoke suite
  --dry-run                    Print configuration and the command that would run without actually executing
  --help                       Show this help message

Examples:
  ./run_live_smoke.sh
  ./run_live_smoke.sh --keyword "MacBook Air M1" --min-items 2
  ./run_live_smoke.sh --without-generation
  ./run_live_smoke.sh -- -k live_real_traffic

Notes:
  0. By default the task-creation CRUD integration test runs first, then the tests/live real-traffic smoke suite.
  1. The script automatically sets RUN_LIVE_TESTS=1.
  2. If LIVE_TEST_ACCOUNT_STATE_FILE is not set, the script tries to use the first *.json found under state/.
  3. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 is set by default to avoid interference from locally installed pytest plugins.
  4. LIVE_TEST_DEBUG_LIMIT=1 is set by default so the smoke test scrapes and analyses only 1 new product.
EOF
}

require_value() {
    local option="$1"
    local value="${2:-}"
    if [[ -z "$value" ]]; then
        echo -e "${RED}Error:${NC} ${option} requires a value"
        exit 1
    fi
}

resolve_default_account_file() {
    local first_match=""
    while IFS= read -r file; do
        first_match="$file"
        break
    done < <(find "$SCRIPT_DIR/state" -maxdepth 1 -type f -name '*.json' | sort)
    printf '%s' "$first_match"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keyword)
            require_value "$1" "${2:-}"
            export LIVE_TEST_KEYWORD="$2"
            shift 2
            ;;
        --account-file)
            require_value "$1" "${2:-}"
            export LIVE_TEST_ACCOUNT_STATE_FILE="$2"
            shift 2
            ;;
        --task-name)
            require_value "$1" "${2:-}"
            export LIVE_TEST_TASK_NAME="$2"
            shift 2
            ;;
        --timeout)
            require_value "$1" "${2:-}"
            export LIVE_TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        --min-items)
            require_value "$1" "${2:-}"
            export LIVE_EXPECT_MIN_ITEMS="$2"
            shift 2
            ;;
        --debug-limit)
            require_value "$1" "${2:-}"
            export LIVE_TEST_DEBUG_LIMIT="$2"
            shift 2
            ;;
        --with-generation)
            WITH_GENERATION=true
            shift
            ;;
        --without-generation)
            WITH_GENERATION=false
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            PYTEST_ARGS+=("$@")
            break
            ;;
        *)
            PYTEST_ARGS+=("$1")
            shift
            ;;
    esac
done

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo -e "${RED}Error:${NC} Python command not found: $PYTHON_CMD"
    exit 1
fi

if ! "$PYTHON_CMD" -m pytest --version >/dev/null 2>&1; then
    echo -e "${RED}Error:${NC} pytest is not available in the current Python environment"
    exit 1
fi

if ! "$PYTHON_CMD" -m playwright --version >/dev/null 2>&1; then
    echo -e "${RED}Error:${NC} Playwright is not available in the current Python environment; please install browser dependencies first"
    exit 1
fi

export RUN_LIVE_TESTS=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD="${PYTEST_DISABLE_PLUGIN_AUTOLOAD:-1}"
export LIVE_TEST_KEYWORD="${LIVE_TEST_KEYWORD:-MacBook Pro M2}"
export LIVE_TEST_TASK_NAME="${LIVE_TEST_TASK_NAME:-Live Smoke Task}"
export LIVE_EXPECT_MIN_ITEMS="${LIVE_EXPECT_MIN_ITEMS:-1}"
export LIVE_TEST_DEBUG_LIMIT="${LIVE_TEST_DEBUG_LIMIT:-1}"
export LIVE_TIMEOUT_SECONDS="${LIVE_TIMEOUT_SECONDS:-180}"

if [[ -z "${LIVE_TEST_ACCOUNT_STATE_FILE:-}" ]]; then
    DEFAULT_ACCOUNT_FILE="$(resolve_default_account_file)"
    if [[ -n "$DEFAULT_ACCOUNT_FILE" ]]; then
        export LIVE_TEST_ACCOUNT_STATE_FILE="$DEFAULT_ACCOUNT_FILE"
    fi
fi

if [[ -z "${LIVE_TEST_ACCOUNT_STATE_FILE:-}" ]]; then
    echo -e "${RED}Error:${NC} No live login state file found. Use --account-file to specify one, or place a *.json file under state/"
    exit 1
fi

if [[ ! -f "${LIVE_TEST_ACCOUNT_STATE_FILE}" ]]; then
    echo -e "${RED}Error:${NC} Login state file does not exist: ${LIVE_TEST_ACCOUNT_STATE_FILE}"
    exit 1
fi

if [[ "$WITH_GENERATION" == "true" ]]; then
    export LIVE_ENABLE_TASK_GENERATION=1
    MARK_EXPRESSION=""
else
    export LIVE_ENABLE_TASK_GENERATION=0
    MARK_EXPRESSION="not live_slow"
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Goofish Real-Traffic Live Smoke Test${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${YELLOW}Python:${NC} $PYTHON_CMD"
echo -e "${YELLOW}Keyword:${NC} ${LIVE_TEST_KEYWORD}"
echo -e "${YELLOW}Task name:${NC} ${LIVE_TEST_TASK_NAME}"
echo -e "${YELLOW}Login state:${NC} ${LIVE_TEST_ACCOUNT_STATE_FILE}"
echo -e "${YELLOW}Min results:${NC} ${LIVE_EXPECT_MIN_ITEMS}"
echo -e "${YELLOW}Scrape/analyse product limit:${NC} ${LIVE_TEST_DEBUG_LIMIT}"
echo -e "${YELLOW}Timeout (seconds):${NC} ${LIVE_TIMEOUT_SECONDS}"
echo -e "${YELLOW}Task generation slow cases:${NC} ${LIVE_ENABLE_TASK_GENERATION}"
echo -e "${YELLOW}Task creation pre-test:${NC} ${TASK_CREATE_TEST}"
if [[ -n "$MARK_EXPRESSION" ]]; then
    echo -e "${YELLOW}Pytest Marker:${NC} ${MARK_EXPRESSION}"
else
    echo -e "${YELLOW}Pytest Marker:${NC} <none>"
fi
echo -e "${YELLOW}Disable plugin autoload:${NC} ${PYTEST_DISABLE_PLUGIN_AUTOLOAD}"

CMD=(
    "$PYTHON_CMD" -m pytest
    "${TEST_TARGETS[@]}"
    -v
)

if [[ -n "$MARK_EXPRESSION" ]]; then
    CMD+=(-m "$MARK_EXPRESSION")
fi

if [[ ${#PYTEST_ARGS[@]} -gt 0 ]]; then
    CMD+=("${PYTEST_ARGS[@]}")
fi

echo -e "${YELLOW}Command:${NC} ${CMD[*]}"

if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${GREEN}Dry run complete; no tests were actually executed.${NC}"
    exit 0
fi

"${CMD[@]}"
