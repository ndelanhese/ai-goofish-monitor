import json
import os
import sys
from typing import Awaitable, Callable, Optional

import aiofiles

from src.infrastructure.external.ai_client import AIClient

# The meta-prompt to instruct the AI
META_PROMPT_TEMPLATE = """
You are a world-class AI prompt engineering expert. Your task is to generate a new [Analysis Criteria] text for the Goofish monitor bot's AI analysis module (codename EagleEye), based on the user's [Purchase Requirements], modeled on a [Reference Example].

Your output must strictly follow the structure, tone, and core principles of the [Reference Example], but the content must be fully tailored to the user's [Purchase Requirements]. The generated text will serve as a thinking guide for the AI analysis module.

---
This is the [Reference Example] (`macbook_criteria.txt`):
```text
{reference_text}
```
---

This is the user's [Purchase Requirements]:
```text
{user_description}
```
---

Please generate the new [Analysis Criteria] text now. Note:
1.  **Output only the generated text content**, without any additional explanations, headings, or code block markers.
2.  Preserve version markers like `[V6.3 Core Upgrade]`, `[V6.4 Logic Fix]` from the example to maintain formatting consistency.
3.  Replace all MacBook-related content in the example with content relevant to the user's target product.
4.  Think carefully and generate "hard veto principles" and a "danger signal checklist" tailored to the new product type.
"""

ProgressCallback = Callable[[str, str], Awaitable[None]]


async def _report_progress(
    progress_callback: Optional[ProgressCallback],
    step_key: str,
    message: str,
) -> None:
    if progress_callback:
        await progress_callback(step_key, message)


def _read_reference_text(reference_file_path: str) -> str:
    try:
        with open(reference_file_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Reference file not found: {reference_file_path}")
    except IOError as exc:
        raise IOError(f"Failed to read reference file: {exc}")


async def _request_generated_text(ai_client: AIClient, prompt: str) -> str:
    print("Calling AI to generate new analysis criteria, please wait...")
    try:
        generated_text = await ai_client._call_ai(
            [{"role": "user", "content": prompt}],
            temperature=0.5,
            max_output_tokens=800,
            enable_json_output=False,
        )
    except Exception as exc:
        print(f"Error calling OpenAI API: {exc}")
        raise

    print("AI successfully generated content.")
    return generated_text.strip()


async def _close_ai_client(
    ai_client: AIClient,
    active_error: BaseException | None,
) -> None:
    try:
        await ai_client.close()
    except Exception as close_error:
        print(f"Error closing AI client: {close_error}")
        if active_error is None:
            raise


async def generate_criteria(
    user_description: str,
    reference_file_path: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """
    Generates a new criteria file content using AI.
    """
    ai_client = AIClient()
    active_error: BaseException | None = None
    try:
        if not ai_client.is_available():
            ai_client.refresh()
        if not ai_client.is_available():
            raise RuntimeError("AI client not initialized. Cannot generate analysis criteria. Please check .env config.")

        await _report_progress(progress_callback, "reference", "Reading reference file.")
        print(f"Reading reference file: {reference_file_path}")
        reference_text = _read_reference_text(reference_file_path)

        await _report_progress(progress_callback, "prompt", "Building AI instructions.")
        print("Building instructions for AI...")
        prompt = META_PROMPT_TEMPLATE.format(
            reference_text=reference_text,
            user_description=user_description,
        )

        await _report_progress(progress_callback, "llm", "Calling AI to generate analysis criteria.")
        return await _request_generated_text(ai_client, prompt)
    except Exception as exc:
        active_error = exc
        raise
    finally:
        await _close_ai_client(ai_client, active_error)


async def update_config_with_new_task(new_task: dict, config_file: str = "config.json"):
    """
    Adds a new task to the specified JSON configuration file.
    """
    print(f"Updating config file: {config_file}")
    try:
        # Read the existing configuration
        config_data = []
        if os.path.exists(config_file):
            async with aiofiles.open(config_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                # Handle the case of an empty file
                if content.strip():
                    try:
                        config_data = json.loads(content)
                        print(f"Successfully read existing config, current task count: {len(config_data)}")
                    except json.JSONDecodeError as e:
                        print(f"Failed to parse config file, will create new config: {e}")
                        config_data = []
        else:
            print(f"Config file not found, will create new file: {config_file}")

        # Append the new task
        config_data.append(new_task)

        # Write the config file back
        async with aiofiles.open(config_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config_data, ensure_ascii=False, indent=2))
            print(f"Config file write complete")

        print(f"Success! New task '{new_task.get('task_name')}' added to {config_file} and enabled.")
        return True
    except json.JSONDecodeError as e:
        error_msg = f"Error: Config file {config_file} is malformed and cannot be parsed: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        return False
    except IOError as e:
        error_msg = f"Error: Failed to read/write config file: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        return False
    except Exception as e:
        error_msg = f"Error: Unknown error updating config file: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        return False
