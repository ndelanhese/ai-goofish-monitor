import asyncio
import sys
import os
import argparse
import json
import signal
import contextlib
import re

from src.config import STATE_FILE
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.scraper import scrape_xianyu


async def main():
    parser = argparse.ArgumentParser(
        description="Goofish product monitoring script with multi-task configuration and real-time AI analysis.",
        epilog="""
Examples:
  # Run all tasks defined in config.json
  python spider_v2.py

  # Run only the task named "Sony A7M4" (typically invoked by the scheduler)
  python spider_v2.py --task-name "Sony A7M4"

  # Debug mode: run all tasks but process only the first 3 newly found products per task
  python spider_v2.py --debug-limit 3
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--debug-limit", type=int, default=0, help="Debug mode: process only the first N new products per task (0 means unlimited)")
    parser.add_argument("--config", type=str, help="Path to the task configuration file (JSON takes priority when provided)")
    parser.add_argument("--task-name", type=str, help="Run only the task with this name (used for scheduled task dispatch)")
    args = parser.parse_args()

    if args.config:
        if not os.path.exists(args.config):
            sys.exit(f"Error: configuration file '{args.config}' does not exist.")
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                tasks_config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            sys.exit(f"Error: failed to read or parse configuration file '{args.config}': {e}")
    else:
        repository = SqliteTaskRepository()
        tasks = await repository.find_all()
        tasks_config = [task.dict() for task in tasks]

    def normalize_keywords(value):
        if value is None:
            return []
        if isinstance(value, str):
            raw_values = re.split(r"[\n,]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        else:
            raw_values = [value]

        normalized = []
        seen = set()
        for item in raw_values:
            text = str(item).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    def flatten_legacy_groups(groups):
        merged = []
        for group in groups or []:
            if isinstance(group, dict):
                merged.extend(normalize_keywords(group.get("include_keywords")))
        return normalize_keywords(merged)

    def has_bound_account(tasks: list) -> bool:
        for task in tasks:
            account = task.get("account_state_file")
            if isinstance(account, str) and account.strip():
                return True
        return False

    def has_any_state_file() -> bool:
        state_dir = os.getenv("ACCOUNT_STATE_DIR", "state").strip().strip('"').strip("'")
        if os.path.isdir(state_dir):
            for name in os.listdir(state_dir):
                if name.endswith(".json"):
                    return True
        return False

    if not os.path.exists(STATE_FILE) and not has_bound_account(tasks_config) and not has_any_state_file():
        sys.exit(
            f"Error: no login state file found. Please add an account under state/ or set account_state_file."
        )

    # Load all prompt file contents (keyword mode does not require loading a prompt)
    for task in tasks_config:
        decision_mode = str(task.get("decision_mode", "ai")).strip().lower()
        if decision_mode not in {"ai", "keyword"}:
            decision_mode = "ai"
        task["decision_mode"] = decision_mode
        keyword_rules = task.get("keyword_rules")
        if keyword_rules is None and task.get("keyword_rule_groups") is not None:
            task["keyword_rules"] = flatten_legacy_groups(task.get("keyword_rule_groups") or [])
        else:
            task["keyword_rules"] = normalize_keywords(keyword_rules)

        if decision_mode == "keyword":
            task["ai_prompt_text"] = ""
            continue

        if task.get("enabled", False) and task.get("ai_prompt_base_file") and task.get("ai_prompt_criteria_file"):
            try:
                with open(task["ai_prompt_base_file"], 'r', encoding='utf-8') as f_base:
                    base_prompt = f_base.read()
                with open(task["ai_prompt_criteria_file"], 'r', encoding='utf-8') as f_criteria:
                    criteria_text = f_criteria.read()
                
                # Dynamically assemble the final prompt
                task['ai_prompt_text'] = base_prompt.replace("{{CRITERIA_SECTION}}", criteria_text)

                # Validate the generated prompt
                if len(task['ai_prompt_text']) < 100:
                    print(f"Warning: task '{task['task_name']}' generated a prompt that is too short ({len(task['ai_prompt_text'])} chars); there may be an issue.")
                elif "{{CRITERIA_SECTION}}" in task['ai_prompt_text']:
                    print(f"Warning: task '{task['task_name']}' prompt still contains the placeholder; substitution may have failed.")
                else:
                    print(f"OK task '{task['task_name']}' prompt generated successfully, length: {len(task['ai_prompt_text'])} chars")

            except FileNotFoundError as e:
                print(f"Warning: task '{task['task_name']}' is missing a prompt file: {e}; AI analysis for this task will be skipped.")
                task['ai_prompt_text'] = ""
            except Exception as e:
                print(f"Error: an exception occurred while processing prompt files for task '{task['task_name']}': {e}; AI analysis for this task will be skipped.")
                task['ai_prompt_text'] = ""
        elif task.get("enabled", False) and task.get("ai_prompt_file"):
            try:
                with open(task["ai_prompt_file"], 'r', encoding='utf-8') as f:
                    task['ai_prompt_text'] = f.read()
                print(f"OK task '{task['task_name']}' prompt file loaded successfully, length: {len(task['ai_prompt_text'])} chars")
            except FileNotFoundError:
                print(f"Warning: prompt file '{task['ai_prompt_file']}' for task '{task['task_name']}' not found; AI analysis for this task will be skipped.")
                task['ai_prompt_text'] = ""
            except Exception as e:
                print(f"Error: an exception occurred while reading the prompt file for task '{task['task_name']}': {e}; AI analysis for this task will be skipped.")
                task['ai_prompt_text'] = ""

    print("\n--- Starting monitoring tasks ---")
    if args.debug_limit > 0:
        print(f"** Debug mode active: processing at most {args.debug_limit} new products per task **")

    if args.task_name:
        print(f"** Scheduled task mode: running only task '{args.task_name}' **")

    print("--------------------")

    active_task_configs = []
    if args.task_name:
        # If a task name is specified, find only that task
        task_found = next((task for task in tasks_config if task.get('task_name') == args.task_name), None)
        if task_found:
            if task_found.get("enabled", False):
                active_task_configs.append(task_found)
            else:
                print(f"Task '{args.task_name}' is disabled; skipping.")
        else:
            print(f"Error: no task named '{args.task_name}' found in the configuration.")
            return
    else:
        # Otherwise load all enabled tasks as originally planned
        active_task_configs = [task for task in tasks_config if task.get("enabled", False)]

    if not active_task_configs:
        print("No tasks to execute; exiting.")
        return

    # Create an async coroutine for each enabled task
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    tasks = []
    for task_conf in active_task_configs:
        print(f"-> Task '{task_conf['task_name']}' added to the execution queue.")
        tasks.append(asyncio.create_task(scrape_xianyu(task_config=task_conf, debug_limit=args.debug_limit)))

    async def _shutdown_watcher():
        await stop_event.wait()
        print("\nTermination signal received; gracefully shutting down and cancelling all scraper tasks...")
        for t in tasks:
            if not t.done():
                t.cancel()

    shutdown_task = asyncio.create_task(_shutdown_watcher())

    try:
        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        shutdown_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown_task

    print("\n--- All tasks completed ---")
    for i, result in enumerate(results):
        task_name = active_task_configs[i]['task_name']
        if isinstance(result, Exception):
            print(f"Task '{task_name}' terminated due to an exception: {result}")
        else:
            print(f"Task '{task_name}' finished normally; processed {result} new products this run.")

if __name__ == "__main__":
    asyncio.run(main())
