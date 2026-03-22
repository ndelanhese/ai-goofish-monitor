import asyncio
import base64
import json
import os
import re
import sys
import shutil
import traceback
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

# Set stdout encoding to UTF-8 to fix Windows console encoding issues
if sys.platform.startswith('win'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.detach())

from src.config import (
    AI_DEBUG_MODE,
    IMAGE_DOWNLOAD_HEADERS,
    IMAGE_SAVE_DIR,
    TASK_IMAGE_DIR_PREFIX,
    MODEL_NAME,
    ENABLE_RESPONSE_FORMAT,
    client,
)
from src.ai_message_builder import (
    build_analysis_text_prompt,
    build_user_message_content,
)
from src.services.ai_response_parser import (
    EmptyAIResponseError,
    extract_ai_response_content,
    parse_ai_response_json,
)
from src.services.ai_request_compat import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    build_ai_request_params,
    create_ai_response_async,
    is_chat_completions_api_unsupported_error,
    is_json_output_unsupported_error,
    is_responses_api_unsupported_error,
    is_temperature_unsupported_error,
    remove_temperature_param,
)
from src.services.notification_service import build_notification_service
from src.utils import convert_goofish_link, retry_on_failure


def _positive_int(value, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


DEFAULT_IMAGE_DOWNLOAD_CONCURRENCY = max(
    1,
    _positive_int(os.getenv("IMAGE_DOWNLOAD_CONCURRENCY", "3"), 3),
)


def safe_print(text):
    """Safe print function that handles encoding errors."""
    try:
        print(text)
    except UnicodeEncodeError:
        # On encoding error, try ASCII with ignore
        try:
            print(text.encode('ascii', errors='ignore').decode('ascii'))
        except:
            # If still failing, print a simplified message
            print("[Output contains undisplayable characters]")


def _build_debug_request_summary(api_mode: str, request_params: dict) -> dict:
    summary = {
        "api_mode": api_mode,
        "model": request_params.get("model"),
    }
    if "temperature" in request_params:
        summary["temperature"] = request_params["temperature"]
    if "max_output_tokens" in request_params:
        summary["max_output_tokens"] = request_params["max_output_tokens"]
    if "max_tokens" in request_params:
        summary["max_tokens"] = request_params["max_tokens"]
    if "text" in request_params:
        summary["text"] = request_params["text"]
    if "response_format" in request_params:
        summary["response_format"] = request_params["response_format"]
    if "input" in request_params:
        summary["input_content_types"] = [
            [item.get("type") for item in message.get("content", [])]
            for message in request_params["input"]
        ]
    if "messages" in request_params:
        summary["message_content_types"] = [
            _extract_message_content_types(message)
            for message in request_params["messages"]
        ]
    return summary


def _extract_message_content_types(message: dict) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return ["text"]
    if not isinstance(content, list):
        return [type(content).__name__]
    return [str(item.get("type")) for item in content if isinstance(item, dict)]


@retry_on_failure(retries=2, delay=3)
async def _download_single_image(url, save_path):
    """Internal function with retry for async single image download."""
    loop = asyncio.get_running_loop()
    # Use run_in_executor to run sync requests code without blocking the event loop
    response = await loop.run_in_executor(
        None,
        lambda: requests.get(url, headers=IMAGE_DOWNLOAD_HEADERS, timeout=20, stream=True)
    )
    response.raise_for_status()
    with open(save_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return save_path


def _build_image_save_path(
    product_id: str,
    index: int,
    url: str,
    task_image_dir: str,
) -> str:
    clean_url = url.split('.heic')[0] if '.heic' in url else url
    file_name_base = os.path.basename(clean_url).split('?')[0]
    file_name = f"product_{product_id}_{index}_{file_name_base}"
    file_name = re.sub(r'[\\/*?:"<>|]', "", file_name)
    if not os.path.splitext(file_name)[1]:
        file_name += ".jpg"
    return os.path.join(task_image_dir, file_name)


async def download_all_images(product_id, image_urls, task_name="default", concurrency=None):
    """Async download of all images for a product. Skips existing. Supports task isolation."""
    if not image_urls:
        return []

    # Create an isolated image directory per task
    task_image_dir = os.path.join(IMAGE_SAVE_DIR, f"{TASK_IMAGE_DIR_PREFIX}{task_name}")
    os.makedirs(task_image_dir, exist_ok=True)

    urls = [url.strip() for url in image_urls if url.strip().startswith('http')]
    if not urls:
        return []

    max_concurrency = _positive_int(concurrency, DEFAULT_IMAGE_DOWNLOAD_CONCURRENCY)
    semaphore = asyncio.Semaphore(max_concurrency)
    total_images = len(urls)

    async def _download_one(index: int, url: str):
        save_path = _build_image_save_path(product_id, index, url, task_image_dir)
        if os.path.exists(save_path):
            safe_print(
                f"   [Image] Image {index}/{total_images} already exists, skipping download: {os.path.basename(save_path)}"
            )
            return save_path
        async with semaphore:
            safe_print(f"   [Image] Downloading image {index}/{total_images}: {url}")
            if await _download_single_image(url, save_path):
                safe_print(
                    f"   [Image] Image {index}/{total_images} downloaded successfully to: {os.path.basename(save_path)}"
                )
                return save_path
        return None

    tasks = [
        asyncio.create_task(_download_one(index, url))
        for index, url in enumerate(urls, start=1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    saved_paths = []
    for url, result in zip(urls, results):
        try:
            if isinstance(result, Exception):
                raise result
            if result:
                saved_paths.append(result)
        except Exception as e:
            safe_print(f"   [Image] Error processing image {url}, skipping: {e}")

    return saved_paths


def cleanup_task_images(task_name):
    """Clean up the image directory for the specified task."""
    task_image_dir = os.path.join(IMAGE_SAVE_DIR, f"{TASK_IMAGE_DIR_PREFIX}{task_name}")
    if os.path.exists(task_image_dir):
        try:
            shutil.rmtree(task_image_dir)
            safe_print(f"   [Cleanup] Deleted temp image dir for task '{task_name}': {task_image_dir}")
        except Exception as e:
            safe_print(f"   [Cleanup] Error deleting temp image dir for task '{task_name}': {e}")
    else:
        safe_print(f"   [Cleanup] Temp image dir for task '{task_name}' does not exist: {task_image_dir}")


def cleanup_ai_logs(logs_dir: str, keep_days: int = 1) -> None:
    try:
        cutoff = datetime.now() - timedelta(days=keep_days)
        for filename in os.listdir(logs_dir):
            if not filename.endswith(".log"):
                continue
            try:
                timestamp = datetime.strptime(filename[:15], "%Y%m%d_%H%M%S")
            except ValueError:
                continue
            if timestamp < cutoff:
                os.remove(os.path.join(logs_dir, filename))
    except Exception as e:
        safe_print(f"   [Log] Error cleaning AI logs: {e}")


def encode_image_to_base64(image_path):
    """Encode a local image file to a Base64 string."""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        safe_print(f"Error encoding image: {e}")
        return None


def validate_ai_response_format(parsed_response):
    """Validate that the AI response format matches the expected structure."""
    required_fields = [
        "prompt_version",
        "is_recommended",
        "reason",
        "risk_tags",
        "criteria_analysis"
    ]

    # Check top-level fields
    for field in required_fields:
        if field not in parsed_response:
            safe_print(f"   [AI] Warning: response missing required field '{field}'")
            return False

    # Check that criteria_analysis is a non-empty dict
    criteria_analysis = parsed_response.get("criteria_analysis", {})
    if not isinstance(criteria_analysis, dict) or not criteria_analysis:
        safe_print("   [AI] Warning: criteria_analysis must be a non-empty dict")
        return False

    # Check seller_type field (required for all products)
    if "seller_type" not in criteria_analysis:
        safe_print("   [AI] Warning: criteria_analysis missing required field 'seller_type'")
        return False

    # Check data types
    if not isinstance(parsed_response.get("is_recommended"), bool):
        safe_print("   [AI] Warning: is_recommended field is not boolean")
        return False

    if not isinstance(parsed_response.get("risk_tags"), list):
        safe_print("   [AI] Warning: risk_tags field is not a list")
        return False

    return True


@retry_on_failure(retries=3, delay=5)
async def send_ntfy_notification(product_data, reason):
    """Compatibility wrapper; internally uses NotificationService."""
    service = build_notification_service()
    if not service.clients:
        safe_print(
            "Warning: No notification service configured in .env file, skipping notification."
        )
        return {}

    results = await service.send_notification(product_data, reason)
    for channel, result in results.items():
        if result["success"]:
            safe_print(f"   -> {channel} notification sent successfully.")
            continue
        safe_print(f"   -> {channel} notification failed: {result['message']}")
    return results


async def get_ai_analysis(product_data, image_paths=None, prompt_text=""):
    """Send complete product JSON data and all images to AI for analysis (async)."""
    if not client:
        safe_print("   [AI] Error: AI client not initialized, skipping analysis.")
        return None

    item_info = product_data.get('product_info', {})
    product_id = item_info.get('item_id', 'N/A')

    safe_print(f"\n   [AI] Analyzing product #{product_id} (with {len(image_paths or [])} image(s))...")
    safe_print(f"   [AI] Title: {item_info.get('product_title', 'N/A')}")

    if not prompt_text:
        safe_print("   [AI] Error: No prompt text provided for AI analysis.")
        return None

    product_details_json = json.dumps(product_data, ensure_ascii=False, indent=2)
    system_prompt = prompt_text

    if AI_DEBUG_MODE:
        safe_print("\n--- [AI DEBUG] ---")
        safe_print("--- PRODUCT DATA (JSON) ---")
        safe_print(product_details_json)
        safe_print("--- PROMPT TEXT (full content) ---")
        safe_print(prompt_text)
        safe_print("-------------------\n")

    image_data_urls = []
    if image_paths:
        for path in image_paths:
            base64_image = encode_image_to_base64(path)
            if base64_image:
                image_data_urls.append(f"data:image/jpeg;base64,{base64_image}")

    combined_text_prompt = build_analysis_text_prompt(
        product_details_json,
        system_prompt,
        include_images=bool(image_data_urls),
    )
    user_content = build_user_message_content(combined_text_prompt, image_data_urls)
    messages = [{"role": "user", "content": user_content}]

    # Save final transmission content to log file
    try:
        # Create logs directory
        logs_dir = os.path.join("logs", "ai")
        os.makedirs(logs_dir, exist_ok=True)
        cleanup_ai_logs(logs_dir, keep_days=1)

        # Generate log filename (current time)
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"{current_time}.log"
        log_filepath = os.path.join(logs_dir, log_filename)

        task_name = product_data.get("task_name") or "unknown"
        log_payload = {
            "timestamp": current_time,
            "task_name": task_name,
            "product_id": product_id,
            "title": item_info.get("product_title", "N/A"),
            "image_count": len(image_data_urls),
        }
        log_content = json.dumps(log_payload, ensure_ascii=False)

        # Write log file
        with open(log_filepath, 'w', encoding='utf-8') as f:
            f.write(log_content)

        safe_print(f"   [Log] AI analysis request saved to: {log_filepath}")

    except Exception as e:
        safe_print(f"   [Log] Error saving AI analysis log: {e}")

    # Enhanced AI call with stricter structured output control and retry logic
    max_retries = 4
    api_mode = CHAT_COMPLETIONS_API_MODE
    use_response_format = ENABLE_RESPONSE_FORMAT
    use_temperature = True
    for attempt in range(max_retries):
        try:
            # Adjust params based on retry count
            current_temperature = 0.1 if attempt == 0 else 0.05  # Use lower temperature on retry

            from src.config import get_ai_request_params

            request_params = build_ai_request_params(
                api_mode,
                model=MODEL_NAME,
                messages=messages,
                temperature=current_temperature,
                max_output_tokens=4000,
                enable_json_output=use_response_format,
            )
            if not use_temperature:
                request_params = remove_temperature_param(request_params)

            request_params = get_ai_request_params(**request_params)

            if AI_DEBUG_MODE:
                safe_print(f"\n--- [AI DEBUG] Attempt {attempt + 1} REQUEST ---")
                safe_print(
                    json.dumps(
                        _build_debug_request_summary(api_mode, request_params),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                safe_print("-----------------------------------\n")

            response = await create_ai_response_async(
                client,
                api_mode,
                request_params,
            )
            ai_response_content = extract_ai_response_content(response)

            if AI_DEBUG_MODE:
                safe_print(f"\n--- [AI DEBUG] Attempt {attempt + 1} ---")
                safe_print("--- RAW AI RESPONSE ---")
                safe_print(ai_response_content)
                safe_print("---------------------\n")

            try:
                parsed_response = parse_ai_response_json(ai_response_content)

                # Validate response format
                if validate_ai_response_format(parsed_response):
                    safe_print(f"   [AI] Attempt {attempt + 1} succeeded, response format validated")
                    return parsed_response
                safe_print(f"   [AI] Attempt {attempt + 1} format validation failed")
                if attempt < max_retries - 1:
                    safe_print(f"   [AI] Preparing retry attempt {attempt + 2}...")
                    continue
                raise ValueError("AI response format is missing required fields or field types are incorrect.")
            except json.JSONDecodeError as e:
                safe_print(f"   [AI] Attempt {attempt + 1} JSON parse failed: {e}")
                if attempt < max_retries - 1:
                    safe_print(f"   [AI] Preparing retry attempt {attempt + 2}...")
                    continue
                raise e
            except EmptyAIResponseError as e:
                safe_print(f"   [AI] Attempt {attempt + 1} returned empty response: {e}")
                if attempt < max_retries - 1:
                    safe_print(f"   [AI] Preparing retry attempt {attempt + 2}...")
                    continue
                raise e

        except Exception as e:
            if (
                api_mode == CHAT_COMPLETIONS_API_MODE
                and is_chat_completions_api_unsupported_error(e)
            ):
                api_mode = RESPONSES_API_MODE
                safe_print(
                    "   [AI] Current service does not implement Chat Completions API, retries will fall back to Responses API."
                )
            elif api_mode == RESPONSES_API_MODE and is_responses_api_unsupported_error(e):
                api_mode = CHAT_COMPLETIONS_API_MODE
                safe_print(
                    "   [AI] Current service does not implement Responses API, retries will fall back to Chat Completions API."
                )
            if use_response_format and is_json_output_unsupported_error(e):
                use_response_format = False
                safe_print(
                    "   [AI] Current model does not support structured JSON output, retries will disable this parameter."
                )
            if use_temperature and is_temperature_unsupported_error(e):
                use_temperature = False
                safe_print(
                    "   [AI] Current model does not support temperature parameter, retries will disable it."
                )
            if AI_DEBUG_MODE:
                safe_print(f"\n--- [AI DEBUG] Attempt {attempt + 1} EXCEPTION ---")
                safe_print(repr(e))
                safe_print(traceback.format_exc())
                safe_print("-------------------------------------\n")
            safe_print(f"   [AI] Attempt {attempt + 1} AI call failed: {e}")
            if attempt < max_retries - 1:
                safe_print(f"   [AI] Preparing retry attempt {attempt + 2}...")
                continue
            else:
                raise e
