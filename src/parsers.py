import json
from datetime import datetime

from src.config import AI_DEBUG_MODE
from src.utils import safe_get


async def _parse_search_results_json(json_data: dict, source: str) -> list:
    """Parse search API JSON data and return a list of basic product info."""
    page_data = []
    try:
        items = await safe_get(json_data, "data", "resultList", default=[])
        if not items:
            print(f"LOG: ({source}) No product list found in API response (resultList).")
            if AI_DEBUG_MODE:
                print(f"--- [SEARCH DEBUG] RAW JSON RESPONSE from {source} ---")
                print(json.dumps(json_data, ensure_ascii=False, indent=2))
                print("----------------------------------------------------")
            return []

        for item in items:
            main_data = await safe_get(item, "data", "item", "main", "exContent", default={})
            click_params = await safe_get(item, "data", "item", "main", "clickParam", "args", default={})

            title = await safe_get(main_data, "title", default="Unknown Title")
            price_parts = await safe_get(main_data, "price", default=[])
            price = "".join([str(p.get("text", "")) for p in price_parts if isinstance(p, dict)]).replace("当前价", "").strip() if isinstance(price_parts, list) else "Price Error"
            if "万" in price: price = f"¥{float(price.replace('¥', '').replace('万', '')) * 10000:.0f}"
            area = await safe_get(main_data, "area", default="Unknown Region")
            seller = await safe_get(main_data, "userNickName", default="Anonymous Seller")
            raw_link = await safe_get(item, "data", "item", "main", "targetUrl", default="")
            image_url = await safe_get(main_data, "picUrl", default="")
            pub_time_ts = click_params.get("publishTime", "")
            item_id = await safe_get(main_data, "itemId", default="Unknown ID")
            original_price = await safe_get(main_data, "oriPrice", default="N/A")
            wants_count = await safe_get(click_params, "wantNum", default='NaN')


            tags = []
            if await safe_get(click_params, "tag") == "freeship":
                tags.append("Free Shipping")
            r1_tags = await safe_get(main_data, "fishTags", "r1", "tagList", default=[])
            for tag_item in r1_tags:
                content = await safe_get(tag_item, "data", "content", default="")
                if "验货宝" in content:
                    tags.append("Verified")

            page_data.append({
                "product_title": title,
                "current_price": price,
                "original_price": original_price,
                "wants_count": wants_count,
                "product_tags": tags,
                "shipping_region": area,
                "seller_nickname": seller,
                "product_link": raw_link.replace("fleamarket://", "https://www.goofish.com/"),
                "publish_time": datetime.fromtimestamp(int(pub_time_ts)/1000).strftime("%Y-%m-%d %H:%M") if pub_time_ts.isdigit() else "Unknown Time",
                "item_id": item_id
            })
        print(f"LOG: ({source}) Successfully parsed {len(page_data)} product entries.")
        return page_data
    except Exception as e:
        print(f"LOG: ({source}) JSON data processing error: {str(e)}")
        return []


async def calculate_reputation_from_ratings(ratings_json: list) -> dict:
    """Calculate positive rating counts and rates as seller and buyer from raw ratings API data."""
    seller_total = 0
    seller_positive = 0
    buyer_total = 0
    buyer_positive = 0

    for card in ratings_json:
        # Use safe_get for safe access
        data = await safe_get(card, 'cardData', default={})
        role_tag = await safe_get(data, 'rateTagList', 0, 'text', default='')
        rate_type = await safe_get(data, 'rate')  # 1=positive, 0=neutral, -1=negative

        if "卖家" in role_tag:
            seller_total += 1
            if rate_type == 1:
                seller_positive += 1
        elif "买家" in role_tag:
            buyer_total += 1
            if rate_type == 1:
                buyer_positive += 1

    # Calculate rates, handling division by zero
    seller_rate = f"{(seller_positive / seller_total * 100):.2f}%" if seller_total > 0 else "N/A"
    buyer_rate = f"{(buyer_positive / buyer_total * 100):.2f}%" if buyer_total > 0 else "N/A"

    return {
        "seller_positive_ratings": f"{seller_positive}/{seller_total}",
        "seller_positive_rate": seller_rate,
        "buyer_positive_ratings": f"{buyer_positive}/{buyer_total}",
        "buyer_positive_rate": buyer_rate
    }


async def _parse_user_items_data(items_json: list) -> list:
    """Parse the product list JSON data from a user's profile page API."""
    parsed_list = []
    for card in items_json:
        data = card.get('cardData', {})
        status_code = data.get('itemStatus')
        if status_code == 0:
            status_text = "For Sale"
        elif status_code == 1:
            status_text = "Sold"
        else:
            status_text = f"Unknown Status ({status_code})"

        parsed_list.append({
            "item_id": data.get('id'),
            "item_title": data.get('title'),
            "item_price": data.get('priceInfo', {}).get('price'),
            "item_image": data.get('picInfo', {}).get('picUrl'),
            "item_status": status_text
        })
    return parsed_list


async def parse_user_head_data(head_json: dict) -> dict:
    """Parse the user header API JSON data."""
    data = head_json.get('data', {})
    ylz_tags = await safe_get(data, 'module', 'base', 'ylzTags', default=[])
    seller_credit, buyer_credit = {}, {}
    for tag in ylz_tags:
        if await safe_get(tag, 'attributes', 'role') == 'seller':
            seller_credit = {'level': await safe_get(tag, 'attributes', 'level'), 'text': tag.get('text')}
        elif await safe_get(tag, 'attributes', 'role') == 'buyer':
            buyer_credit = {'level': await safe_get(tag, 'attributes', 'level'), 'text': tag.get('text')}
    return {
        "seller_nickname": await safe_get(data, 'module', 'base', 'displayName'),
        "seller_avatar": await safe_get(data, 'module', 'base', 'avatar', 'avatar'),
        "seller_bio": await safe_get(data, 'module', 'base', 'introduction', default=''),
        "seller_items_count": await safe_get(data, 'module', 'tabs', 'item', 'number'),
        "seller_total_ratings": await safe_get(data, 'module', 'tabs', 'rate', 'number'),
        "seller_credit_level": seller_credit.get('text', 'N/A'),
        "buyer_credit_level": buyer_credit.get('text', 'N/A')
    }


async def parse_ratings_data(ratings_json: list) -> list:
    """Parse the ratings list API JSON data."""
    parsed_list = []
    for card in ratings_json:
        data = await safe_get(card, 'cardData', default={})
        rate_tag = await safe_get(data, 'rateTagList', 0, 'text', default='Unknown Role')
        rate_type = await safe_get(data, 'rate')
        if rate_type == 1: rate_text = "Positive"
        elif rate_type == 0: rate_text = "Neutral"
        elif rate_type == -1: rate_text = "Negative"
        else: rate_text = "Unknown"
        parsed_list.append({
            "rating_id": data.get('rateId'),
            "rating_content": data.get('feedback'),
            "rating_type": rate_text,
            "rater_role": rate_tag,
            "rater_nickname": data.get('raterUserNick'),
            "rating_time": data.get('gmtCreate'),
            "rating_images": await safe_get(data, 'pictCdnUrlList', default=[])
        })
    return parsed_list
