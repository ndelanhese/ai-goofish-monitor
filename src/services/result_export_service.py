"""
Result export service
"""
import csv
from io import StringIO


EXPORT_HEADERS = [
    "task_name",
    "search_keyword",
    "item_id",
    "product_title",
    "current_price",
    "publish_time",
    "seller_nickname",
    "ai_recommended",
    "analysis_source",
    "reason",
    "observation_count",
    "min_price",
    "max_price",
    "market_avg_price",
    "value_score",
    "value_summary",
    "product_link",
]


def build_results_csv(records: list[dict]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_HEADERS)
    writer.writeheader()

    for record in records:
        item = record.get("product_info", {}) or {}
        seller = record.get("seller_info", {}) or {}
        ai_analysis = record.get("ai_analysis", {}) or {}
        price_insight = record.get("price_insight", {}) or {}
        writer.writerow(
            {
                "task_name": record.get("task_name", ""),
                "search_keyword": record.get("search_keyword", ""),
                "item_id": item.get("item_id", ""),
                "product_title": item.get("product_title", ""),
                "current_price": item.get("current_price", ""),
                "publish_time": item.get("publish_time", ""),
                "seller_nickname": seller.get("seller_nickname") or item.get("seller_nickname", ""),
                "ai_recommended": "yes" if ai_analysis.get("is_recommended") else "no",
                "analysis_source": ai_analysis.get("analysis_source", ""),
                "reason": ai_analysis.get("reason", ""),
                "observation_count": price_insight.get("observation_count", ""),
                "min_price": price_insight.get("min_price", ""),
                "max_price": price_insight.get("max_price", ""),
                "market_avg_price": price_insight.get("market_avg_price", ""),
                "value_score": ai_analysis.get("value_score", price_insight.get("deal_score", "")),
                "value_summary": ai_analysis.get("value_summary", price_insight.get("deal_label", "")),
                "product_link": item.get("product_link", ""),
            }
        )

    return buffer.getvalue()
