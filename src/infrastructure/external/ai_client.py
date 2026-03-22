"""
AI client wrapper.
Provides a unified interface for AI API calls.
"""
import os
import json
import base64
from typing import Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI
from src.ai_message_builder import (
    build_analysis_text_prompt,
    build_user_message_content,
)
from src.infrastructure.config.settings import AISettings
from src.infrastructure.config.env_manager import env_manager
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
from src.services.ai_response_parser import (
    EmptyAIResponseError,
    extract_ai_response_content,
    parse_ai_response_json,
)


class AIClient:
    """AI client wrapper."""

    def __init__(self):
        self.settings: Optional[AISettings] = None
        self.client: Optional[AsyncOpenAI] = None
        self.refresh()

    def _load_settings(self) -> None:
        load_dotenv(dotenv_path=env_manager.env_file, override=True)
        self.settings = AISettings()

    def refresh(self) -> None:
        self._load_settings()
        self.client = self._initialize_client()

    def _initialize_client(self) -> Optional[AsyncOpenAI]:
        """Initialize the OpenAI client."""
        if not self.settings or not self.settings.is_configured():
            print("Warning: AI configuration is incomplete; AI features will be unavailable")
            return None

        try:
            if self.settings.proxy_url:
                print(f"Using proxy for AI requests: {self.settings.proxy_url}")
                os.environ['HTTP_PROXY'] = self.settings.proxy_url
                os.environ['HTTPS_PROXY'] = self.settings.proxy_url

            return AsyncOpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url
            )
        except Exception as e:
            print(f"Failed to initialize AI client: {e}")
            return None

    def is_available(self) -> bool:
        """Check whether the AI client is available."""
        return self.client is not None

    async def close(self) -> None:
        """Close the underlying async client to avoid cleanup after the event loop ends."""
        client = self.client
        self.client = None
        if client is None:
            return

        close = getattr(client, "close", None)
        if close is None:
            return
        await close()

    @staticmethod
    def encode_image(image_path: str) -> Optional[str]:
        """Encode an image file to Base64."""
        if not image_path or not os.path.exists(image_path):
            return None
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            print(f"Failed to encode image: {e}")
            return None

    async def analyze(
        self,
        product_data: Dict,
        image_paths: List[str],
        prompt_text: str
    ) -> Optional[Dict]:
        """
        Analyze product data.

        Args:
            product_data: Product data dictionary.
            image_paths: List of image file paths.
            prompt_text: Analysis prompt text.

        Returns:
            Analysis result dictionary, or None on failure.
        """
        if not self.is_available():
            print("AI client is not available")
            return None

        try:
            messages = self._build_messages(product_data, image_paths, prompt_text)
            response = await self._call_ai(messages)
            return self._parse_response(response)
        except Exception as e:
            print(f"AI analysis failed: {e}")
            return None

    def _build_messages(self, product_data: Dict, image_paths: List[str], prompt_text: str) -> List[Dict]:
        """Build AI messages."""
        product_json = json.dumps(product_data, ensure_ascii=False, indent=2)
        image_data_urls: List[str] = []
        for path in image_paths:
            base64_img = self.encode_image(path)
            if base64_img:
                image_data_urls.append(f"data:image/jpeg;base64,{base64_img}")

        text_prompt = build_analysis_text_prompt(
            product_json,
            prompt_text,
            include_images=bool(image_data_urls),
        )
        user_content = build_user_message_content(text_prompt, image_data_urls)
        return [{"role": "user", "content": user_content}]

    async def _call_ai(
        self,
        messages: List[Dict],
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 4000,
        enable_json_output: Optional[bool] = None,
    ) -> str:
        """Call the AI API."""
        api_mode = CHAT_COMPLETIONS_API_MODE
        use_response_format = (
            self.settings.enable_response_format
            if enable_json_output is None
            else enable_json_output
        )
        use_temperature = True
        max_attempts = 4

        for attempt in range(max_attempts):
            request_params = build_ai_request_params(
                api_mode,
                model=self.settings.model_name,
                messages=messages,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                enable_json_output=use_response_format,
            )
            if not use_temperature:
                request_params = remove_temperature_param(request_params)

            if self.settings.enable_thinking:
                request_params["extra_body"] = {"enable_thinking": False}

            try:
                response = await create_ai_response_async(
                    self.client,
                    api_mode,
                    request_params,
                )
                return extract_ai_response_content(response)
            except EmptyAIResponseError as exc:
                if attempt < max_attempts - 1:
                    print(
                        f"AI response was empty, retrying automatically ({attempt + 2}/{max_attempts})"
                    )
                    continue
                raise exc
            except Exception as exc:
                changed = False
                if (
                    api_mode == CHAT_COMPLETIONS_API_MODE
                    and is_chat_completions_api_unsupported_error(exc)
                ):
                    api_mode = RESPONSES_API_MODE
                    changed = True
                    print("Chat Completions API is not supported by this service; falling back to Responses API")
                elif (
                    api_mode == RESPONSES_API_MODE
                    and is_responses_api_unsupported_error(exc)
                ):
                    api_mode = CHAT_COMPLETIONS_API_MODE
                    changed = True
                    print("Responses API is not supported by this service; falling back to Chat Completions API")
                if use_response_format and is_json_output_unsupported_error(exc):
                    use_response_format = False
                    changed = True
                    print("Structured JSON output is not supported by this model; retrying without that parameter")
                if use_temperature and is_temperature_unsupported_error(exc):
                    use_temperature = False
                    changed = True
                    print("temperature parameter is not supported by this model; retrying without it")
                if changed and attempt < max_attempts - 1:
                    continue
                raise

        raise RuntimeError("AI call did not return a result after compatibility retries")

    def _parse_response(self, response_text: str) -> Optional[Dict]:
        """Parse the AI response."""
        try:
            return parse_ai_response_json(response_text)
        except json.JSONDecodeError:
            print(f"Failed to parse AI response: {response_text[:100]}")
            return None
