import asyncio
import json
import os
import random
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import (
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.ai_handler import (
    download_all_images,
    get_ai_analysis,
    send_ntfy_notification,
    cleanup_task_images,
)
from src.config import (
    AI_DEBUG_MODE,
    DETAIL_API_URL_PATTERN,
    LOGIN_IS_EDGE,
    RUN_HEADLESS,
    RUNNING_IN_DOCKER,
    SKIP_AI_ANALYSIS,
    STATE_FILE,
)
from src.parsers import (
    _parse_search_results_json,
    _parse_user_items_data,
    calculate_reputation_from_ratings,
    parse_ratings_data,
    parse_user_head_data,
)
from src.utils import (
    format_registration_days,
    get_link_unique_key,
    log_time,
    random_sleep,
    safe_get,
    save_to_jsonl,
)
from src.rotation import RotationPool, load_state_files, parse_proxy_pool, RotationItem
from src.failure_guard import FailureGuard
from src.services.account_strategy_service import resolve_account_runtime_plan
from src.infrastructure.persistence.storage_names import build_result_filename
from src.services.item_analysis_dispatcher import (
    ItemAnalysisDispatcher,
    ItemAnalysisJob,
)
from src.services.price_history_service import (
    build_market_reference,
    load_price_snapshots,
    record_market_snapshots,
)
from src.services.result_storage_service import load_processed_link_keys
from src.services.seller_profile_cache import SellerProfileCache
from src.services.search_pagination import (
    advance_search_page,
    is_search_results_response,
)


class RiskControlError(Exception):
    pass


class LoginRequiredError(Exception):
    """Raised when Goofish redirects to the passport/mini_login flow."""


FAILURE_GUARD = FailureGuard()
EDGE_DOCKER_WARNING_PRINTED = False


def _is_login_url(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "passport.goofish.com" in lowered or "mini_login" in lowered


def _resolve_browser_channel() -> str:
    global EDGE_DOCKER_WARNING_PRINTED
    if RUNNING_IN_DOCKER:
        if LOGIN_IS_EDGE and not EDGE_DOCKER_WARNING_PRINTED:
            print(
                "LOGIN_IS_EDGE=true detected, but the Docker image does not include Edge. "
                "Chromium will be used instead."
            )
            EDGE_DOCKER_WARNING_PRINTED = True
        return "chromium"
    return "msedge" if LOGIN_IS_EDGE else "chrome"


def _should_analyze_images(task_config: dict) -> bool:
    raw_value = task_config.get("analyze_images", True)
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() not in {"false", "0", "no", "off"}


def _format_failure_reason(reason: str, limit: int = 500) -> str:
    if not reason:
        return "Unknown error"
    cleaned = " ".join(str(reason).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


async def _notify_task_failure(
    task_config: dict, reason: str, *, cookie_path: Optional[str]
) -> None:
    task_name = task_config.get("task_name", "Untitled Task")
    keyword = task_config.get("keyword", "")
    formatted_reason = _format_failure_reason(reason)

    # Some failures are deterministic misconfiguration and should pause/notify immediately.
    pause_immediately = any(
        marker in formatted_reason
        for marker in (
            "No available proxy address found",
            "No available login state file found",
        )
    )

    guard_result = FAILURE_GUARD.record_failure(
        task_name,
        formatted_reason,
        cookie_path=cookie_path,
        min_failures_to_pause=1 if pause_immediately else None,
    )

    if not guard_result.get("should_notify"):
        print(
            f"[FailureGuard] Task '{task_name}' failure count {guard_result.get('consecutive_failures')}/{FAILURE_GUARD.threshold}, skipping notification."
        )
        return

    paused_until = guard_result.get("paused_until")
    paused_until_str = (
        paused_until.strftime("%Y-%m-%d %H:%M:%S") if paused_until else "N/A"
    )

    product_data = {
        "product_title": f"[Task Error] {task_name}",
        "current_price": "N/A",
        "product_link": "#",
    }
    notify_reason = (
        f"Task failed (consecutive failures: {guard_result.get('consecutive_failures')}/{FAILURE_GUARD.threshold}): {formatted_reason}"
        f"\nTask: {task_name}"
        f"\nKeyword: {keyword or 'N/A'}"
        f"\nAuto-paused until: {paused_until_str}"
        f"\nWill auto-resume after updating login state/cookies file."
    )

    try:
        await send_ntfy_notification(product_data, notify_reason)
    except Exception as e:
        print(f"Failed to send task error notification: {e}")


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_rotation_settings(task_config: dict) -> dict:
    account_cfg = task_config.get("account_rotation") or {}
    proxy_cfg = task_config.get("proxy_rotation") or {}

    account_enabled = _as_bool(
        account_cfg.get("enabled"),
        _as_bool(os.getenv("ACCOUNT_ROTATION_ENABLED"), False),
    )
    account_mode = (
        account_cfg.get("mode") or os.getenv("ACCOUNT_ROTATION_MODE", "per_task")
    ).lower()
    account_state_dir = account_cfg.get("state_dir") or os.getenv(
        "ACCOUNT_STATE_DIR", "state"
    )
    account_retry_limit = _as_int(
        account_cfg.get("retry_limit"),
        _as_int(os.getenv("ACCOUNT_ROTATION_RETRY_LIMIT"), 2),
    )
    account_blacklist_ttl = _as_int(
        account_cfg.get("blacklist_ttl_sec"),
        _as_int(os.getenv("ACCOUNT_BLACKLIST_TTL"), 300),
    )

    proxy_enabled = _as_bool(
        proxy_cfg.get("enabled"), _as_bool(os.getenv("PROXY_ROTATION_ENABLED"), False)
    )
    proxy_mode = (
        proxy_cfg.get("mode") or os.getenv("PROXY_ROTATION_MODE", "per_task")
    ).lower()
    proxy_pool = proxy_cfg.get("proxy_pool") or os.getenv("PROXY_POOL", "")
    proxy_retry_limit = _as_int(
        proxy_cfg.get("retry_limit"),
        _as_int(os.getenv("PROXY_ROTATION_RETRY_LIMIT"), 2),
    )
    proxy_blacklist_ttl = _as_int(
        proxy_cfg.get("blacklist_ttl_sec"),
        _as_int(os.getenv("PROXY_BLACKLIST_TTL"), 300),
    )

    return {
        "account_enabled": account_enabled,
        "account_mode": account_mode,
        "account_state_dir": account_state_dir,
        "account_retry_limit": max(1, account_retry_limit),
        "account_blacklist_ttl": max(0, account_blacklist_ttl),
        "proxy_enabled": proxy_enabled,
        "proxy_mode": proxy_mode,
        "proxy_pool": proxy_pool,
        "proxy_retry_limit": max(1, proxy_retry_limit),
        "proxy_blacklist_ttl": max(0, proxy_blacklist_ttl),
    }


def _get_ai_analysis_concurrency(task_config: dict) -> int:
    configured = task_config.get("ai_analysis_concurrency")
    default = _as_int(os.getenv("AI_ANALYSIS_CONCURRENCY"), 2)
    return max(1, _as_int(configured, default))


def _get_seller_profile_cache_ttl(task_config: dict) -> int:
    configured = task_config.get("seller_profile_cache_ttl")
    default = _as_int(os.getenv("SELLER_PROFILE_CACHE_TTL"), 1800)
    return max(0, _as_int(configured, default))


def _default_context_options() -> dict:
    return {
        "user_agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "permissions": ["geolocation"],
        "geolocation": {"longitude": 121.4737, "latitude": 31.2304},
        "color_scheme": "light",
    }


def _clean_kwargs(options: dict) -> dict:
    return {k: v for k, v in options.items() if v is not None}


def _looks_like_mobile(ua: str) -> Optional[bool]:
    if not ua:
        return None
    ua_lower = ua.lower()
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        return True
    if "windows" in ua_lower or "macintosh" in ua_lower:
        return False
    return None


def _build_context_overrides(snapshot: dict) -> dict:
    env = snapshot.get("env") or {}
    headers = snapshot.get("headers") or {}
    navigator = env.get("navigator") or {}
    screen = env.get("screen") or {}
    intl = env.get("intl") or {}

    overrides = {}

    ua = (
        headers.get("User-Agent")
        or headers.get("user-agent")
        or navigator.get("userAgent")
    )
    if ua:
        overrides["user_agent"] = ua

    accept_language = headers.get("Accept-Language") or headers.get("accept-language")
    locale = None
    if accept_language:
        locale = accept_language.split(",")[0].strip()
    elif navigator.get("language"):
        locale = navigator["language"]
    if locale:
        overrides["locale"] = locale

    tz = intl.get("timeZone")
    if tz:
        overrides["timezone_id"] = tz

    width = screen.get("width")
    height = screen.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        overrides["viewport"] = {"width": int(width), "height": int(height)}

    dpr = screen.get("devicePixelRatio")
    if isinstance(dpr, (int, float)):
        overrides["device_scale_factor"] = float(dpr)

    touch_points = navigator.get("maxTouchPoints")
    if isinstance(touch_points, (int, float)):
        overrides["has_touch"] = touch_points > 0

    mobile_flag = _looks_like_mobile(ua or "")
    if mobile_flag is not None:
        overrides["is_mobile"] = mobile_flag

    return _clean_kwargs(overrides)


def _build_extra_headers(raw_headers: Optional[dict]) -> dict:
    if not raw_headers:
        return {}
    excluded = {"cookie", "content-length"}
    headers = {}
    for key, value in raw_headers.items():
        if not key or key.lower() in excluded or value is None:
            continue
        headers[key] = value
    return headers


async def scrape_user_profile(context, user_id: str) -> dict:
    """
    Visit the specified user's profile page and collect summary info,
    full product list, and full ratings list in order.
    """
    print(f"   -> Starting data collection for user ID: {user_id}...")
    profile_data = {}
    page = await context.new_page()

    # Prepare futures and containers for async tasks
    head_api_future = asyncio.get_event_loop().create_future()

    all_items, all_ratings = [], []
    stop_item_scrolling, stop_rating_scrolling = asyncio.Event(), asyncio.Event()

    async def handle_response(response: Response):
        # Capture user head summary API
        if (
            "mtop.idle.web.user.page.head" in response.url
            and not head_api_future.done()
        ):
            try:
                head_api_future.set_result(await response.json())
                print(f"      [API Captured] User head info... success")
            except Exception as e:
                if not head_api_future.done():
                    head_api_future.set_exception(e)

        # Capture product list API
        elif "mtop.idle.web.xyh.item.list" in response.url:
            try:
                data = await response.json()
                all_items.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API Captured] Product list... {len(all_items)} items so far")
                if not data.get("data", {}).get("nextPage", True):
                    stop_item_scrolling.set()
            except Exception as e:
                stop_item_scrolling.set()

        # Capture ratings list API
        elif "mtop.idle.web.trade.rate.list" in response.url:
            try:
                data = await response.json()
                all_ratings.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API Captured] Ratings list... {len(all_ratings)} entries so far")
                if not data.get("data", {}).get("nextPage", True):
                    stop_rating_scrolling.set()
            except Exception as e:
                stop_rating_scrolling.set()

    page.on("response", handle_response)

    try:
        # --- Task 1: Navigate and collect head info ---
        await page.goto(
            f"https://www.goofish.com/personal?userId={user_id}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        head_data = await asyncio.wait_for(head_api_future, timeout=15)
        profile_data = await parse_user_head_data(head_data)

        # --- Task 2: Scroll and load all products (default tab) ---
        print("      [Collection] Loading seller's product list...")
        await random_sleep(2, 4)  # Wait for first page of products API
        while not stop_item_scrolling.is_set():
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            try:
                await asyncio.wait_for(stop_item_scrolling.wait(), timeout=8)
            except asyncio.TimeoutError:
                print("      [Scroll Timeout] Product list may be fully loaded.")
                break
        profile_data["seller_items"] = await _parse_user_items_data(all_items)

        # --- Task 3: Click and collect all ratings ---
        print("      [Collection] Loading seller's ratings list...")
        rating_tab_locator = page.locator("//div[text()='信用及评价']/ancestor::li")
        if await rating_tab_locator.count() > 0:
            await rating_tab_locator.click()
            await random_sleep(3, 5)  # Wait for first page of ratings API

            while not stop_rating_scrolling.is_set():
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    await asyncio.wait_for(stop_rating_scrolling.wait(), timeout=8)
                except asyncio.TimeoutError:
                    print("      [Scroll Timeout] Ratings list may be fully loaded.")
                    break

            profile_data["seller_ratings"] = await parse_ratings_data(all_ratings)
            reputation_stats = await calculate_reputation_from_ratings(all_ratings)
            profile_data.update(reputation_stats)
        else:
            print("      [Warning] Ratings tab not found, skipping ratings collection.")

    except Exception as e:
        print(f"   [Error] Failed to collect data for user {user_id}: {e}")
    finally:
        page.remove_listener("response", handle_response)
        await page.close()
        print(f"   -> Data collection for user {user_id} complete.")

    return profile_data


async def scrape_xianyu(task_config: dict, debug_limit: int = 0):
    """
    Core executor.
    Asynchronously scrapes Goofish product data based on a single task config,
    performing real-time, independent AI analysis and notifications for each new product found.
    """
    keyword = task_config["keyword"]
    max_pages = task_config.get("max_pages", 1)
    personal_only = task_config.get("personal_only", False)
    min_price = task_config.get("min_price")
    max_price = task_config.get("max_price")
    ai_prompt_text = task_config.get("ai_prompt_text", "")
    analyze_images = _should_analyze_images(task_config)
    decision_mode = str(task_config.get("decision_mode", "ai")).strip().lower()
    if decision_mode not in {"ai", "keyword"}:
        decision_mode = "ai"
    keyword_rules = task_config.get("keyword_rules") or []
    free_shipping = task_config.get("free_shipping", False)
    raw_new_publish = task_config.get("new_publish_option") or ""
    new_publish_option = raw_new_publish.strip()
    if new_publish_option == "__none__":
        new_publish_option = ""
    region_filter = (task_config.get("region") or "").strip()

    processed_links = set()
    history_run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    history_seen_item_ids: set[str] = set()
    historical_snapshots = load_price_snapshots(keyword)
    result_filename = build_result_filename(keyword)
    processed_links = load_processed_link_keys(keyword)
    if processed_links:
        print(f"LOG: Found existing result set {result_filename}, loaded {len(processed_links)} historical items for deduplication.")
    else:
        print(f"LOG: Result set {result_filename} is empty, new records will be written.")

    rotation_settings = _get_rotation_settings(task_config)
    account_items = load_state_files(rotation_settings["account_state_dir"])
    runtime_plan = resolve_account_runtime_plan(
        strategy=task_config.get("account_strategy"),
        account_state_file=task_config.get("account_state_file"),
        has_root_state_file=os.path.exists(STATE_FILE),
        available_account_files=account_items,
    )
    forced_account = runtime_plan["forced_account"]
    if runtime_plan["prefer_root_state"]:
        account_items = [STATE_FILE]
        rotation_settings["account_enabled"] = False
    elif runtime_plan["use_account_pool"]:
        rotation_settings["account_enabled"] = True
    else:
        rotation_settings["account_enabled"] = False

    account_pool = RotationPool(
        account_items, rotation_settings["account_blacklist_ttl"], "account"
    )
    proxy_pool = RotationPool(
        parse_proxy_pool(rotation_settings["proxy_pool"]),
        rotation_settings["proxy_blacklist_ttl"],
        "proxy",
    )

    selected_account: Optional[RotationItem] = None
    selected_proxy: Optional[RotationItem] = None

    def _select_account(force_new: bool = False) -> Optional[RotationItem]:
        nonlocal selected_account
        if forced_account:
            return RotationItem(value=forced_account)
        if not rotation_settings["account_enabled"]:
            if os.path.exists(STATE_FILE):
                return RotationItem(value=STATE_FILE)
            return None
        if (
            rotation_settings["account_mode"] == "per_task"
            and selected_account
            and not force_new
        ):
            return selected_account
        picked = account_pool.pick_random()
        return picked or selected_account

    def _select_proxy(force_new: bool = False) -> Optional[RotationItem]:
        nonlocal selected_proxy
        if not rotation_settings["proxy_enabled"]:
            return None
        if (
            rotation_settings["proxy_mode"] == "per_task"
            and selected_proxy
            and not force_new
        ):
            return selected_proxy
        picked = proxy_pool.pick_random()
        return picked or selected_proxy

    async def _run_scrape_attempt(state_file: str, proxy_server: Optional[str]) -> int:
        processed_item_count = 0
        stop_scraping = False

        if not os.path.exists(state_file):
            raise FileNotFoundError(f"Login state file not found: {state_file}")

        snapshot_data = None
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to read login state file, will use path directly: {e}")

        async with async_playwright() as p:
            # Anti-detection launch arguments
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]

            launch_kwargs = {"headless": RUN_HEADLESS, "args": launch_args}
            if proxy_server:
                launch_kwargs["proxy"] = {"server": proxy_server}

            launch_kwargs["channel"] = _resolve_browser_channel()

            browser = await p.chromium.launch(**launch_kwargs)

            context_kwargs = _default_context_options()
            storage_state_arg = state_file
            analysis_dispatcher: Optional[ItemAnalysisDispatcher] = None

            if isinstance(snapshot_data, dict):
                # Enhanced snapshot from the new extension export, includes env and headers
                if any(
                    key in snapshot_data
                    for key in ("env", "headers", "page", "storage")
                ):
                    print(f"Enhanced browser snapshot detected, applying environment params: {state_file}")
                    storage_state_arg = {"cookies": snapshot_data.get("cookies", [])}
                    context_kwargs.update(_build_context_overrides(snapshot_data))
                    extra_headers = _build_extra_headers(snapshot_data.get("headers"))
                    if extra_headers:
                        context_kwargs["extra_http_headers"] = extra_headers
                else:
                    storage_state_arg = snapshot_data

            context_kwargs = _clean_kwargs(context_kwargs)
            context = await browser.new_context(
                storage_state=storage_state_arg, **context_kwargs
            )
            seller_profile_cache = SellerProfileCache(
                ttl_seconds=_get_seller_profile_cache_ttl(task_config)
            )
            analysis_dispatcher = ItemAnalysisDispatcher(
                concurrency=_get_ai_analysis_concurrency(task_config),
                skip_ai_analysis=SKIP_AI_ANALYSIS,
                seller_loader=lambda user_id: seller_profile_cache.get_or_load(
                    str(user_id),
                    lambda seller_key: scrape_user_profile(context, seller_key),
                ),
                image_downloader=download_all_images,
                ai_analyzer=get_ai_analysis,
                notifier=send_ntfy_notification,
                saver=save_to_jsonl,
            )

            # Enhanced anti-detection script (simulates a real mobile device)
            await context.add_init_script("""
                // Remove webdriver flag
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

                // Simulate real mobile device navigator properties
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});

                // Add chrome object
                window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}};

                // Simulate touch support
                Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 5});

                // Override permissions query (avoid exposing automation)
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({state: Notification.permission}) :
                        originalQuery(parameters)
                );
            """)

            page = await context.new_page()

            try:
                # Step 0 - Simulate real user: visit homepage first (important anti-detection)
                log_time("Step 0 - Simulating real user visiting homepage...")
                await page.goto(
                    "https://www.goofish.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                log_time("[Anti-scrape] Staying on homepage, simulating browsing...")
                await random_sleep(1, 2)

                # Simulate random scrolling (mobile touch scroll)
                await page.evaluate("window.scrollBy(0, Math.random() * 500 + 200)")
                await random_sleep(1, 2)

                log_time("Step 1 - Navigating to search results page...")
                # Build the correct search URL with 'q' param and URL encoding
                params = {"q": keyword}
                search_url = f"https://www.goofish.com/search?{urlencode(params)}"
                log_time(f"Target URL: {search_url}")

                # Listen for search API response before navigating to avoid missing the first request
                async with page.expect_response(
                    is_search_results_response, timeout=30000
                ) as initial_response_info:
                    await page.goto(
                        search_url, wait_until="domcontentloaded", timeout=60000
                    )
                if _is_login_url(page.url):
                    raise LoginRequiredError(
                        f"Login required: redirected to {page.url} (cookies/state likely expired)"
                    )

                # Capture initial search API data
                initial_response = await initial_response_info.value

                # Wait for key filter elements to confirm we're on the search results page
                try:
                    await page.wait_for_selector("text=新发布", timeout=15000)
                except PlaywrightTimeoutError as e:
                    if _is_login_url(page.url):
                        raise LoginRequiredError(
                            f"Login required: redirected to {page.url} (cookies/state likely expired)"
                        ) from e
                    raise

                # Simulate real user behavior: initial pause and browsing after page load
                log_time("[Anti-scrape] Simulating user viewing page...")
                await random_sleep(1, 3)

                # Check for verification popups
                baxia_dialog = page.locator("div.baxia-dialog-mask")
                middleware_widget = page.locator("div.J_MIDDLEWARE_FRAME_WIDGET")
                try:
                    # Wait up to 2s for popup. If it appears, execute block.
                    await baxia_dialog.wait_for(state="visible", timeout=2000)
                    print(
                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                    )
                    print("Goofish anti-scraping verification popup detected (baxia-dialog). Cannot continue.")
                    print("This usually happens due to too-frequent requests or bot detection.")
                    print("Suggestions:")
                    print("1. Stop the script for a while and try again.")
                    print(
                        "2. (Recommended) Set RUN_HEADLESS=false in .env to run in non-headless mode, which helps bypass detection."
                    )
                    print(f"Task '{keyword}' will abort here.")
                    print(
                        "==================================================================="
                    )
                    raise RiskControlError("baxia-dialog")
                except PlaywrightTimeoutError:
                    # Popup did not appear within 2s - normal, continue
                    pass

                # Check for J_MIDDLEWARE_FRAME_WIDGET overlay
                try:
                    await middleware_widget.wait_for(state="visible", timeout=2000)
                    print(
                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                    )
                    print(
                        "Goofish anti-scraping verification popup detected (J_MIDDLEWARE_FRAME_WIDGET). Cannot continue."
                    )
                    print("This usually happens due to too-frequent requests or bot detection.")
                    print("Suggestions:")
                    print("1. Stop the script for a while and try again.")
                    print("2. (Recommended) Update the login state file to ensure it is valid.")
                    print("3. Reduce task frequency to avoid bot detection.")
                    print(f"Task '{keyword}' will abort here.")
                    print(
                        "==================================================================="
                    )
                    raise RiskControlError("J_MIDDLEWARE_FRAME_WIDGET")
                except PlaywrightTimeoutError:
                    # Popup did not appear within 2s - normal, continue
                    pass

                try:
                    await page.click("div[class*='closeIconBg']", timeout=3000)
                    print("LOG: Closed ad popup.")
                except PlaywrightTimeoutError:
                    print("LOG: No ad popup detected.")

                final_response = None
                log_time("Step 2 - Applying filters...")
                if new_publish_option:
                    try:
                        await page.click("text=新发布")
                        await random_sleep(1, 2)  # previously (1.5, 2.5)
                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.click(f"text={new_publish_option}")
                            # Increased wait time after sorting
                            await random_sleep(2, 4)  # previously (3, 5)
                        final_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time(
                            f"New listing filter '{new_publish_option}' request timed out, continuing."
                        )
                    except Exception as e:
                        print(f"LOG: Failed to apply new listing filter: {e}")

                if personal_only:
                    async with page.expect_response(
                        is_search_results_response, timeout=20000
                    ) as response_info:
                        await page.click("text=个人闲置")
                        # Changed fixed wait to random wait and extended it
                        await random_sleep(2, 4)  # previously asyncio.sleep(5)
                    final_response = await response_info.value

                if free_shipping:
                    try:
                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.click("text=包邮")
                            await random_sleep(2, 4)
                        final_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time("Free shipping filter request timed out, continuing.")
                    except Exception as e:
                        print(f"LOG: Failed to apply free shipping filter: {e}")

                if region_filter:
                    try:
                        area_trigger = page.get_by_text("区域", exact=True)
                        if await area_trigger.count():
                            await area_trigger.first.click()
                            await random_sleep(1.5, 2)
                            popover_candidates = page.locator("div.ant-popover")
                            popover = popover_candidates.filter(
                                has=page.locator(
                                    ".areaWrap--FaZHsn8E, [class*='areaWrap']"
                                )
                            ).last
                            if not await popover.count():
                                popover = popover_candidates.filter(
                                    has=page.get_by_text("重新定位")
                                ).last
                            if not await popover.count():
                                popover = popover_candidates.filter(
                                    has=page.get_by_text("查看")
                                ).last
                            if not await popover.count():
                                print("LOG: Region popup not found, skipping region filter.")
                                raise PlaywrightTimeoutError("region-popover-not-found")
                            await popover.wait_for(state="visible", timeout=5000)

                            # List container: first-level children are province/city/district columns - not tied to specific class names for robustness
                            area_wrap = popover.locator(
                                ".areaWrap--FaZHsn8E, [class*='areaWrap']"
                            ).first
                            await area_wrap.wait_for(state="visible", timeout=3000)
                            columns = area_wrap.locator(":scope > div")
                            col_prov = columns.nth(0)
                            col_city = columns.nth(1)
                            col_dist = columns.nth(2)

                            region_parts = [
                                p.strip() for p in region_filter.split("/") if p.strip()
                            ]

                            async def _click_in_column(
                                column_locator, text_value: str, desc: str
                            ) -> None:
                                option = column_locator.locator(
                                    ".provItem--QAdOx8nD", has_text=text_value
                                ).first
                                if await option.count():
                                    await option.click()
                                    await random_sleep(1.5, 2)
                                    try:
                                        await option.wait_for(
                                            state="attached", timeout=1500
                                        )
                                        await option.wait_for(
                                            state="visible", timeout=1500
                                        )
                                    except PlaywrightTimeoutError:
                                        pass
                                else:
                                    print(f"LOG: {desc} '{text_value}' not found, skipping.")

                            if len(region_parts) >= 1:
                                await _click_in_column(
                                    col_prov, region_parts[0], "province"
                                )
                                await random_sleep(1, 2)
                            if len(region_parts) >= 2:
                                await _click_in_column(
                                    col_city, region_parts[1], "city"
                                )
                                await random_sleep(1, 2)
                            if len(region_parts) >= 3:
                                await _click_in_column(
                                    col_dist, region_parts[2], "district"
                                )
                                await random_sleep(1, 2)

                            search_btn = popover.locator(
                                "div.searchBtn--Ic6RKcAb"
                            ).first
                            if await search_btn.count():
                                try:
                                    async with page.expect_response(
                                        is_search_results_response,
                                        timeout=20000,
                                    ) as response_info:
                                        await search_btn.click()
                                        await random_sleep(2, 3)
                                    final_response = await response_info.value
                                except PlaywrightTimeoutError:
                                    log_time("Region filter submit timed out, continuing.")
                            else:
                                print(
                                    "LOG: 'View XX items' button not found in region popup, skipping submit."
                                )
                        else:
                            print("LOG: Region filter trigger not found.")
                    except PlaywrightTimeoutError:
                        log_time(f"Region filter '{region_filter}' request timed out, continuing.")
                    except Exception as e:
                        print(f"LOG: Failed to apply region filter '{region_filter}': {e}")

                if min_price or max_price:
                    price_container = page.locator(
                        'div[class*="search-price-input-container"]'
                    ).first
                    if await price_container.is_visible():
                        if min_price:
                            await price_container.get_by_placeholder("¥").first.fill(
                                min_price
                            )
                            # Changed fixed wait to random wait
                            await random_sleep(1, 2.5)  # previously asyncio.sleep(5)
                        if max_price:
                            await (
                                price_container.get_by_placeholder("¥")
                                .nth(1)
                                .fill(max_price)
                            )
                            # Changed fixed wait to random wait
                            await random_sleep(1, 2.5)  # previously asyncio.sleep(5)

                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.keyboard.press("Tab")
                            # Increased wait time after confirming price
                            await random_sleep(2, 4)  # previously asyncio.sleep(5)
                        final_response = await response_info.value
                    else:
                        print("LOG: Warning - price input container not found.")

                log_time("All filters applied, processing product list...")

                current_response = (
                    final_response
                    if final_response and final_response.ok
                    else initial_response
                )
                for page_num in range(1, max_pages + 1):
                    if stop_scraping:
                        break
                    log_time(f"Processing page {page_num}/{max_pages}...")

                    if page_num > 1:
                        page_advance_result = await advance_search_page(
                            page=page,
                            page_num=page_num,
                        )
                        if not page_advance_result.advanced:
                            break
                        current_response = page_advance_result.response

                    if not (current_response and current_response.ok):
                        log_time(f"Page {page_num} response invalid, skipping.")
                        continue

                    basic_items = await _parse_search_results_json(
                        await current_response.json(), f"Page {page_num}"
                    )
                    if not basic_items:
                        break
                    historical_snapshots.extend(
                        record_market_snapshots(
                            keyword=keyword,
                            task_name=task_config.get("task_name", "Untitled Task"),
                            items=basic_items,
                            run_id=history_run_id,
                            snapshot_time=datetime.now().isoformat(),
                            seen_item_ids=history_seen_item_ids,
                        )
                    )

                    total_items_on_page = len(basic_items)
                    for i, item_data in enumerate(basic_items, 1):
                        if debug_limit > 0 and processed_item_count >= debug_limit:
                            log_time(
                                f"Debug limit reached ({debug_limit}), stopping new product fetch."
                            )
                            stop_scraping = True
                            break

                        unique_key = get_link_unique_key(item_data["product_link"])
                        if unique_key in processed_links:
                            log_time(
                                f"[Page progress {i}/{total_items_on_page}] Product '{item_data['product_title'][:20]}...' already processed, skipping."
                            )
                            continue

                        log_time(
                            f"[Page progress {i}/{total_items_on_page}] New product found, fetching details: {item_data['product_title'][:30]}..."
                        )
                        # Wait before visiting detail page to simulate user browsing the list
                        await random_sleep(2, 4)

                        detail_page = await context.new_page()
                        try:
                            async with detail_page.expect_response(
                                lambda r: DETAIL_API_URL_PATTERN in r.url, timeout=25000
                            ) as detail_info:
                                await detail_page.goto(
                                    item_data["product_link"],
                                    wait_until="domcontentloaded",
                                    timeout=25000,
                                )

                            detail_response = await detail_info.value
                            if detail_response.ok:
                                detail_json = await detail_response.json()

                                ret_string = str(
                                    await safe_get(detail_json, "ret", default=[])
                                )
                                if "FAIL_SYS_USER_VALIDATE" in ret_string:
                                    print(
                                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                                    )
                                    print(
                                        "Goofish anti-scraping verification detected (FAIL_SYS_USER_VALIDATE), aborting."
                                    )
                                    long_sleep_duration = random.randint(3, 60)
                                    print(
                                        f"To avoid account risk, sleeping for {long_sleep_duration} seconds before exiting..."
                                    )
                                    await asyncio.sleep(long_sleep_duration)
                                    print("Long sleep complete, exiting safely.")
                                    print(
                                        "==================================================================="
                                    )
                                    raise RiskControlError("FAIL_SYS_USER_VALIDATE")

                                # Parse product detail data and update item_data
                                item_do = await safe_get(
                                    detail_json, "data", "itemDO", default={}
                                )
                                seller_do = await safe_get(
                                    detail_json, "data", "sellerDO", default={}
                                )

                                reg_days_raw = await safe_get(
                                    seller_do, "userRegDay", default=0
                                )
                                registration_duration_text = format_registration_days(
                                    reg_days_raw
                                )

                                # --- START: new block ---

                                # 1. Extract seller Sesame Credit info
                                zhima_credit_text = await safe_get(
                                    seller_do, "zhimaLevelInfo", "levelName"
                                )

                                # 2. Extract complete image list for this product
                                image_infos = await safe_get(
                                    item_do, "imageInfos", default=[]
                                )
                                if image_infos:
                                    # Use list comprehension to get all valid image URLs
                                    all_image_urls = [
                                        img.get("url")
                                        for img in image_infos
                                        if img.get("url")
                                    ]
                                    if all_image_urls:
                                        # Store image list in new field, replacing the old single link
                                        item_data["image_list"] = all_image_urls
                                        # (Optional) Also keep main image link as fallback
                                        item_data["main_image_url"] = all_image_urls[0]

                                # --- END: new block ---
                                item_data["wants_count"] = await safe_get(
                                    item_do,
                                    "wantCnt",
                                    default=item_data.get("wants_count", "NaN"),
                                )
                                item_data["view_count"] = await safe_get(
                                    item_do, "browseCnt", default="-"
                                )
                                # ...[Add more product info parsed from detail page here]...

                                user_id = await safe_get(seller_do, "sellerId")

                                # Build base record
                                final_record = {
                                    "scraped_at": datetime.now().isoformat(),
                                    "search_keyword": keyword,
                                    "task_name": task_config.get(
                                        "task_name", "Untitled Task"
                                    ),
                                    "product_info": item_data,
                                    "seller_info": {},
                                }
                                price_reference = build_market_reference(
                                    keyword=keyword,
                                    item=item_data,
                                    current_market_items=basic_items,
                                    historical_snapshots=historical_snapshots,
                                )
                                final_record["price_reference"] = price_reference
                                final_record["price_insight"] = price_reference.get(
                                    "item_price_position", {}
                                )

                                analysis_dispatcher.submit(
                                    ItemAnalysisJob(
                                        keyword=keyword,
                                        task_name=task_config.get(
                                            "task_name", "Untitled Task"
                                        ),
                                        decision_mode=decision_mode,
                                        analyze_images=analyze_images,
                                        prompt_text=ai_prompt_text,
                                        keyword_rules=tuple(keyword_rules or []),
                                        final_record=final_record,
                                        seller_id=str(user_id) if user_id else None,
                                        zhima_credit_text=zhima_credit_text,
                                        registration_duration_text=registration_duration_text,
                                    )
                                )

                                processed_links.add(unique_key)
                                processed_item_count += 1
                                log_time(
                                    f"Product submitted for background analysis. Total processed: {processed_item_count} new products."
                                )

                                # Add main delay after processing each product
                                log_time(
                                    "[Anti-scrape] Executing main random delay to simulate user browsing interval..."
                                )
                                await random_sleep(5, 10)
                            else:
                                print(
                                    f"   Error: Failed to get product detail API response, status code: {detail_response.status}"
                                )
                                if AI_DEBUG_MODE:
                                    print(
                                        f"--- [DETAIL DEBUG] FAILED RESPONSE from {item_data['product_link']} ---"
                                    )
                                    try:
                                        print(await detail_response.text())
                                    except Exception as e:
                                        print(f"Cannot read response content: {e}")
                                    print(
                                        "----------------------------------------------------"
                                    )

                        except PlaywrightTimeoutError:
                            print(f"   Error: Timed out accessing product detail page or waiting for API response.")
                        except Exception as e:
                            print(f"   Error: Unknown error processing product detail: {e}")
                        finally:
                            await detail_page.close()
                            # Add brief pause after closing page
                            await random_sleep(2, 4)  # previously (1, 2.5)

                    # After processing all products on a page, add a longer rest before paginating
                    if not stop_scraping and page_num < max_pages:
                        print(
                            f"--- Page {page_num} complete, preparing to turn page. Taking a long inter-page rest... ---"
                        )
                        await random_sleep(10, 15)

            except PlaywrightTimeoutError as e:
                if _is_login_url(page.url):
                    raise LoginRequiredError(
                        f"Login required: redirected to {page.url} (cookies/state likely expired)"
                    ) from e
                print(f"\nOperation timeout: page element or network response did not appear in time.\n{e}")
                raise
            except asyncio.CancelledError:
                log_time("Cancellation signal received, terminating scraper task...")
                raise
            except Exception as e:
                if type(e).__name__ == "TargetClosedError":
                    log_time("Browser closed, ignoring subsequent exceptions (task may have been stopped).")
                    return processed_item_count
                if "passport.goofish.com" in str(e):
                    raise LoginRequiredError(
                        f"Login required: redirected to passport flow ({e})"
                    ) from e
                print(f"\nUnknown error during scraping: {e}")
                raise
            finally:
                if analysis_dispatcher is not None:
                    log_time("Waiting for background analysis tasks to complete...")
                    await analysis_dispatcher.join()
                log_time("Task complete, browser will close automatically in 5 seconds...")
                await asyncio.sleep(5)
                if debug_limit:
                    input("Press Enter to close browser...")
                await browser.close()

        return processed_item_count

    processed_item_count = 0
    attempt_limit = max(
        rotation_settings["account_retry_limit"],
        rotation_settings["proxy_retry_limit"],
        1,
    )
    last_error = ""
    last_state_path: Optional[str] = None

    # If this task is already in a paused state, skip immediately.
    task_name_for_guard = task_config.get("task_name", "Untitled Task")
    pause_cookie_path = None
    if (
        isinstance(task_config.get("account_state_file"), str)
        and task_config.get("account_state_file").strip()
    ):
        pause_cookie_path = task_config.get("account_state_file").strip()
    elif os.path.exists(STATE_FILE):
        pause_cookie_path = STATE_FILE

    decision = FAILURE_GUARD.should_skip_start(
        task_name_for_guard, cookie_path=pause_cookie_path
    )
    if decision.skip:
        print(
            f"[FailureGuard] Task '{task_name_for_guard}' is paused (consecutive failures: {decision.consecutive_failures}/{FAILURE_GUARD.threshold})"
        )
        if decision.should_notify:
            try:
                await send_ntfy_notification(
                    {
                        "product_title": f"[Task Paused] {task_name_for_guard}",
                        "current_price": "N/A",
                        "product_link": "#",
                    },
                    "Task is paused and will be skipped.\n"
                    f"Reason: {decision.reason}\n"
                    f"Consecutive failures: {decision.consecutive_failures}/{FAILURE_GUARD.threshold}\n"
                    f"Paused until: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                    "Fix: task will auto-resume after updating login state/cookies file.",
                )
            except Exception as e:
                print(f"Failed to send task pause notification: {e}")

        cleanup_task_images(task_config.get("task_name", "default"))
        return 0

    for attempt in range(1, attempt_limit + 1):
        if attempt == 1:
            selected_account = _select_account()
            selected_proxy = _select_proxy()
        else:
            if (
                rotation_settings["account_enabled"]
                and rotation_settings["account_mode"] == "on_failure"
            ):
                account_pool.mark_bad(selected_account, last_error)
                selected_account = _select_account(force_new=True)
            if (
                rotation_settings["proxy_enabled"]
                and rotation_settings["proxy_mode"] == "on_failure"
            ):
                proxy_pool.mark_bad(selected_proxy, last_error)
                selected_proxy = _select_proxy(force_new=True)

        if rotation_settings["account_enabled"] and not selected_account:
            last_error = "No available login state file found, cannot continue task."
            print(last_error)
            break
        if not rotation_settings["account_enabled"] and not selected_account:
            last_error = "No available login state file found, cannot continue task."
            print(last_error)
            break
        if rotation_settings["proxy_enabled"] and not selected_proxy:
            last_error = "No available proxy address found, cannot continue task."
            print(last_error)
            break

        state_path = selected_account.value if selected_account else STATE_FILE
        last_state_path = state_path
        proxy_server = selected_proxy.value if selected_proxy else None
        if rotation_settings["account_enabled"]:
            print(f"Account rotation: using login state {state_path}")
        if rotation_settings["proxy_enabled"] and proxy_server:
            print(f"IP rotation: using proxy {proxy_server}")

        try:
            processed_item_count += await _run_scrape_attempt(state_path, proxy_server)
            last_error = ""
            FAILURE_GUARD.record_success(task_name_for_guard)
            break
        except LoginRequiredError as e:
            last_error = str(e)
            print(f"Login expired/redirect detected: {e}")
            break
        except RiskControlError as e:
            last_error = str(e)
            print(f"Risk control or verification triggered: {e}")
            # Risk control usually cannot be resolved by simple rotation; avoid pointless retries.
            break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"Attempt failed: {last_error}")
            if attempt < attempt_limit:
                print("Will retry after rotating account/IP...")

    if last_error:
        await _notify_task_failure(task_config, last_error, cookie_path=last_state_path)

    # Clean up task image directory
    cleanup_task_images(task_config.get("task_name", "default"))

    return processed_item_count
