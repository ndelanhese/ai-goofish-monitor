"""
Helper functions for building AI request messages.
"""
from typing import Dict, List, Union


TEXT_ONLY_ANALYSIS_NOTE = (
    "Note: No product images provided. Please evaluate based solely on the product text fields and seller info. Do not infer image content."
)


def build_analysis_text_prompt(
    product_json: str,
    prompt_text: str,
    *,
    include_images: bool,
) -> str:
    note = "" if include_images else f"\n{TEXT_ONLY_ANALYSIS_NOTE}\n"
    value_note = (
        "\nIf the product JSON contains a price reference or price_insight, please consider the price level, historical trend, "
        "specs, condition, accessories, and seller info. "
        "You may additionally output optional fields value_score(0-100) and value_summary, "
        "but the original is_recommended/reason and other fields must be preserved.\n"
    )
    return f"""Please use your expertise and my requirements to analyze the following complete product JSON data:

```json
{product_json}
```

    {prompt_text}
    {value_note}
    {note}"""


def build_user_message_content(
    text_prompt: str,
    image_data_urls: List[str],
) -> Union[str, List[Dict[str, object]]]:
    if not image_data_urls:
        return text_prompt

    user_content: List[Dict[str, object]] = [
        {"type": "image_url", "image_url": {"url": url}}
        for url in image_data_urls
    ]
    user_content.append({"type": "text", "text": text_prompt})
    return user_content
