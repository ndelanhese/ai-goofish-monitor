import asyncio
import json
import math
import os
import random
import re
import glob
from datetime import datetime
from functools import wraps
from urllib.parse import quote

from openai import APIStatusError
from requests.exceptions import HTTPError

from src.services.result_storage_service import save_result_record


def retry_on_failure(retries=3, delay=5):
    """
    A generic async retry decorator with detailed HTTP error logging.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return await func(*args, **kwargs)
                except (APIStatusError, HTTPError) as e:
                    print(f"Function {func.__name__} attempt {i + 1}/{retries} failed with HTTP error.")
                    if hasattr(e, 'status_code'):
                        print(f"  - Status Code: {e.status_code}")
                    if hasattr(e, 'response') and hasattr(e.response, 'text'):
                        response_text = e.response.text
                        print(
                            f"  - Response: {response_text[:300]}{'...' if len(response_text) > 300 else ''}")
                except json.JSONDecodeError as e:
                    print(f"Function {func.__name__} attempt {i + 1}/{retries} failed: JSON decode error - {e}")
                except Exception as e:
                    print(f"Function {func.__name__} attempt {i + 1}/{retries} failed: {type(e).__name__} - {e}")

                if i < retries - 1:
                    print(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)

            print(f"Function {func.__name__} failed after {retries} attempts.")
            return None
        return wrapper
    return decorator


async def safe_get(data, *keys, default="N/A"):
    """Safely retrieve a value from a nested dict."""
    for key in keys:
        try:
            data = data[key]
        except (KeyError, TypeError, IndexError):
            return default
    return data


async def random_sleep(min_seconds: float, max_seconds: float):
    """Asynchronously sleep for a random duration within the specified range."""
    delay = random.uniform(min_seconds, max_seconds)
    print(f"   [Delay] Waiting {delay:.2f} seconds... (range: {min_seconds}-{max_seconds}s)")
    await asyncio.sleep(delay)


def log_time(message: str, prefix: str = "") -> None:
    """Simple print with a YY-MM-DD HH:MM:SS timestamp prefix."""
    try:
        ts = datetime.now().strftime(' %Y-%m-%d %H:%M:%S')
    except Exception:
        ts = "--:--:--"
    print(f"[{ts}] {prefix}{message}")


def sanitize_filename(value: str) -> str:
    """Generate a safe filename fragment."""
    if not value:
        return "task"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "task"


def build_task_log_path(task_id: int, task_name: str) -> str:
    """Generate the task log path (including task name)."""
    safe_name = sanitize_filename(task_name)
    filename = f"{safe_name}_{task_id}.log"
    return os.path.join("logs", filename)


def resolve_task_log_path(task_id: int, task_name: str) -> str:
    """Prefer the task-name-based log path, falling back to ID-based matching if not found."""
    primary_path = build_task_log_path(task_id, task_name)
    if os.path.exists(primary_path):
        return primary_path
    pattern = os.path.join("logs", f"*_{task_id}.log")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    return primary_path


def convert_goofish_link(url: str) -> str:
    """
    Convert a Goofish product link to the mobile format containing only the item ID.
    """
    match_first_link = re.search(r'item\?id=(\d+)', url)
    if match_first_link:
        item_id = match_first_link.group(1)
        bfp_json = f'{{"id":{item_id}}}'
        return f"https://pages.goofish.com/sharexy?loadingVisible=false&bft=item&bfs=idlepc.item&spm=a21ybx.item.0.0&bfp={quote(bfp_json)}"
    return url


def get_link_unique_key(link: str) -> str:
    """Extract the part of a link before the first '&' as a unique identifier."""
    return link.split('&', 1)[0]


async def save_to_jsonl(data_record: dict, keyword: str):
    """Compatibility wrapper for old call sites; writes results to SQLite."""
    try:
        return await save_result_record(data_record, keyword)
    except Exception as e:
        print(f"Error writing SQLite result record: {e}")
        return False


def format_registration_days(total_days: int) -> str:
    """
    Format a total number of days as a 'X years Y months' string.
    """
    if not isinstance(total_days, int) or total_days <= 0:
        return 'Unknown'

    DAYS_IN_YEAR = 365.25
    DAYS_IN_MONTH = DAYS_IN_YEAR / 12

    years = math.floor(total_days / DAYS_IN_YEAR)
    remaining_days = total_days - (years * DAYS_IN_YEAR)
    months = round(remaining_days / DAYS_IN_MONTH)

    if months == 12:
        years += 1
        months = 0

    if years > 0 and months > 0:
        return f"On Goofish for {years} year(s) {months} month(s)"
    elif years > 0 and months == 0:
        return f"On Goofish for {years} year(s)"
    elif years == 0 and months > 0:
        return f"On Goofish for {months} month(s)"
    else:
        return "On Goofish for less than a month"
