"""
Notification client base class.
Defines the unified interface for notification clients.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict

from src.utils import convert_goofish_link


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    price: str
    reason: str
    desktop_link: str
    mobile_link: str | None
    notification_title: str
    content: str
    image_url: str | None


class NotificationClient(ABC):
    """Abstract base class for notification clients."""

    channel_key = "unknown"
    display_name = "Unknown Channel"

    def __init__(self, enabled: bool = False, pcurl_to_mobile: bool = True):
        self._enabled = enabled
        self._pcurl_to_mobile = pcurl_to_mobile

    def is_enabled(self) -> bool:
        """Check whether the client is enabled."""
        return self._enabled

    @abstractmethod
    async def send(self, product_data: Dict, reason: str) -> bool:
        """
        Send a notification.

        Args:
            product_data: Product data dictionary.
            reason: Recommendation reason.

        Returns:
            True if the notification was sent successfully.
        """
        raise NotImplementedError

    def _build_message(self, product_data: Dict, reason: str) -> NotificationMessage:
        """Format notification message content."""
        title = product_data.get('product_title', 'N/A')
        price = product_data.get('current_price', 'N/A')
        desktop_link = product_data.get('product_link', '#')
        mobile_link = None

        if self._pcurl_to_mobile and desktop_link and desktop_link != "#":
            mobile_link = convert_goofish_link(desktop_link)

        content_lines = [
            f"Price: {price}",
            f"Reason: {reason}",
        ]
        if mobile_link:
            content_lines.append(f"Mobile link: {mobile_link}")
            content_lines.append(f"Desktop link: {desktop_link}")
        else:
            content_lines.append(f"Link: {desktop_link}")

        short_title = title[:30]
        suffix = "..." if len(title) > 30 else ""
        notification_title = f"🚨 New Recommendation! {short_title}{suffix}"

        main_image = product_data.get('main_image_url')
        if not main_image:
            image_list = product_data.get('image_list', [])
            if image_list:
                main_image = image_list[0]

        return NotificationMessage(
            title=title,
            price=price,
            reason=reason,
            desktop_link=desktop_link,
            mobile_link=mobile_link,
            notification_title=notification_title,
            content="\n".join(content_lines),
            image_url=main_image,
        )
