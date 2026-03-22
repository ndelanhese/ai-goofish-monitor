"""
WeCom (Enterprise WeChat) bot notification client.
"""
import asyncio
from typing import Dict

import requests

from .base import NotificationClient


class WeComBotClient(NotificationClient):
    """WeCom bot notification client."""

    channel_key = "wecom"
    display_name = "WeCom"

    def __init__(self, bot_url: str | None = None, pcurl_to_mobile: bool = True):
        super().__init__(enabled=bool(bot_url), pcurl_to_mobile=pcurl_to_mobile)
        self.bot_url = bot_url

    async def send(self, product_data: Dict, reason: str) -> None:
        if not self.is_enabled():
            raise RuntimeError("WeCom is not enabled")

        message = self._build_message(product_data, reason)
        markdown_lines = [f"## {message.notification_title}", ""]
        markdown_lines.append(f"- Price: {message.price}")
        markdown_lines.append(f"- Reason: {message.reason}")
        if message.mobile_link:
            markdown_lines.append(f"- Mobile link: [{message.mobile_link}]({message.mobile_link})")
        markdown_lines.append(f"- Desktop link: [{message.desktop_link}]({message.desktop_link})")
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": "\n".join(markdown_lines)},
        }
        headers = {"Content-Type": "application/json"}
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                self.bot_url,
                json=payload,
                headers=headers,
                timeout=10,
            ),
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errcode", 0) != 0:
            raise RuntimeError(result.get("errmsg", "WeCom returned an unknown error"))
