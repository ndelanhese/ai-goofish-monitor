import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.utils import log_time, random_sleep

NEXT_PAGE_SELECTOR = (
    "button[class*='search-pagination-arrow-container']"
    ":has([class*='search-pagination-arrow-right'])"
    ":not([disabled])"
)
SEARCH_RESULTS_API_FRAGMENT = "/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/"
PAGE_REQUEST_TIMEOUT_MS = 20_000
PAGE_CLICK_TIMEOUT_MS = 10_000
PAGE_RETRY_DELAY_SECONDS = 5
PAGE_RETRY_COUNT = 2
PAGE_CLICK_SLEEP_MIN_SECONDS = 2
PAGE_CLICK_SLEEP_MAX_SECONDS = 5


@dataclass(frozen=True)
class PageAdvanceResult:
    advanced: bool
    response: Optional[Any] = None
    stop_reason: Optional[str] = None


def is_search_results_response(
    response: Any,
    api_url_fragment: str = SEARCH_RESULTS_API_FRAGMENT,
) -> bool:
    request = getattr(response, "request", None)
    request_method = getattr(request, "method", None)
    response_url = getattr(response, "url", "")
    return api_url_fragment in response_url and request_method == "POST"


async def advance_search_page(
    *,
    page: Any,
    page_num: int,
    logger: Callable[[str], None] = log_time,
    wait_after_click: Callable[[float, float], Awaitable[None]] = random_sleep,
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_retries: int = PAGE_RETRY_COUNT,
) -> PageAdvanceResult:
    next_button = page.locator(NEXT_PAGE_SELECTOR).first
    if not await next_button.count():
        logger("Reached the last page, no enabled next-page button found, stopping pagination.")
        return PageAdvanceResult(advanced=False, stop_reason="no_next_button")

    for retry_index in range(max_retries):
        try:
            await next_button.scroll_into_view_if_needed()
            async with page.expect_response(
                is_search_results_response,
                timeout=PAGE_REQUEST_TIMEOUT_MS,
            ) as response_info:
                try:
                    await next_button.click(timeout=PAGE_CLICK_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    logger(f"Page {page_num} next-page button click timed out, stopping pagination.")
                    return PageAdvanceResult(
                        advanced=False,
                        stop_reason="click_timeout",
                    )
            await wait_after_click(
                PAGE_CLICK_SLEEP_MIN_SECONDS,
                PAGE_CLICK_SLEEP_MAX_SECONDS,
            )
            return PageAdvanceResult(
                advanced=True,
                response=await response_info.value,
            )
        except PlaywrightTimeoutError:
            if retry_index < max_retries - 1:
                logger(
                    f"Timed out waiting for page {page_num} search response, "
                    f"retrying in {PAGE_RETRY_DELAY_SECONDS}s..."
                )
                await retry_sleep(PAGE_RETRY_DELAY_SECONDS)
                continue

            logger(f"Timed out waiting for page {page_num} search response after {max_retries} retries, stopping pagination.")
            return PageAdvanceResult(advanced=False, stop_reason="response_timeout")

    return PageAdvanceResult(advanced=False, stop_reason="unknown")
