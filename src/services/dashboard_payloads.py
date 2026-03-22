"""
Dashboard data assembly helper functions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.domain.models.task import Task
from src.services.price_history_service import parse_price_value
from src.services.result_file_service import (
    normalize_keyword_from_filename,
)
from src.services.result_storage_service import load_result_summary


def normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
      return None
    normalized = value.strip()
    for candidate in (normalized, normalized.replace("Z", "+00:00"), normalized.replace(" ", "T")):
      try:
        return datetime.fromisoformat(candidate)
      except ValueError:
        continue
    return None


def serialize_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def build_empty_summary(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "task_name": task.task_name,
        "keyword": task.keyword,
        "filename": None,
        "enabled": task.enabled,
        "is_running": task.is_running,
        "account_strategy": task.account_strategy,
        "cron": task.cron,
        "region": task.region,
        "total_items": 0,
        "recommended_items": 0,
        "ai_recommended_items": 0,
        "keyword_recommended_items": 0,
        "latest_crawl_time": None,
        "latest_recommended_title": None,
        "latest_recommended_price": None,
    }


def build_activity(
    *,
    activity_id: str,
    activity_type: str,
    task_name: str,
    keyword: str,
    title: str,
    status: str,
    timestamp: datetime | None,
    detail: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    return {
        "id": activity_id,
        "type": activity_type,
        "task_name": task_name,
        "keyword": keyword,
        "title": title,
        "status": status,
        "detail": detail,
        "filename": filename,
        "timestamp": serialize_timestamp(timestamp),
    }


def sort_key_by_latest_time(item: dict[str, Any]) -> tuple[float, str]:
    timestamp = parse_timestamp(item.get("latest_crawl_time"))
    return (timestamp.timestamp() if timestamp else 0.0, item.get("task_name", ""))


def sort_key_by_activity_time(item: dict[str, Any]) -> tuple[float, str]:
    timestamp = parse_timestamp(item.get("timestamp"))
    return (timestamp.timestamp() if timestamp else 0.0, item.get("id", ""))


def _build_fallback_summary(task_name: str, keyword: str) -> dict[str, Any]:
    return {
        "task_id": None,
        "task_name": task_name,
        "keyword": keyword,
        "filename": None,
        "enabled": False,
        "is_running": False,
        "account_strategy": "auto",
        "cron": None,
        "region": None,
        "total_items": 0,
        "recommended_items": 0,
        "ai_recommended_items": 0,
        "keyword_recommended_items": 0,
        "latest_crawl_time": None,
        "latest_recommended_title": None,
        "latest_recommended_price": None,
    }


def _resolve_task(
    task_lookup: dict[str, Task],
    latest_record: dict[str, Any] | None,
    keyword: str,
) -> Task | None:
    task = task_lookup.get(normalize_text(keyword))
    if task is not None or latest_record is None:
        return task
    fallback_name = str(latest_record.get("task_name") or "")
    return next(
        (candidate for candidate in task_lookup.values() if candidate.task_name == fallback_name),
        None,
    )


def _collect_record_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest_crawl_time: datetime | None = None
    latest_record: dict[str, Any] | None = None
    latest_recommendation: dict[str, Any] | None = None
    recommended_items = 0
    ai_recommended_items = 0
    keyword_recommended_items = 0

    for record in records:
        crawl_time = parse_timestamp(record.get("scraped_at"))
        if crawl_time and (latest_crawl_time is None or crawl_time > latest_crawl_time):
            latest_crawl_time = crawl_time
            latest_record = record

        analysis = record.get("ai_analysis", {}) or {}
        if analysis.get("is_recommended") is not True:
            continue

        recommended_items += 1
        source = analysis.get("analysis_source")
        if source == "ai":
            ai_recommended_items += 1
        elif source == "keyword":
            keyword_recommended_items += 1

        recommendation_time = parse_timestamp(
            latest_recommendation.get("scraped_at") if latest_recommendation else None
        )
        if latest_recommendation is None or (crawl_time and recommendation_time and crawl_time > recommendation_time):
            latest_recommendation = record
        elif latest_recommendation is None and crawl_time:
            latest_recommendation = record

    return {
        "latest_crawl_time": latest_crawl_time,
        "latest_record": latest_record,
        "latest_recommendation": latest_recommendation,
        "recommended_items": recommended_items,
        "ai_recommended_items": ai_recommended_items,
        "keyword_recommended_items": keyword_recommended_items,
    }


def _build_recommendation_activity(
    *,
    filename: str,
    task_name: str,
    keyword: str,
    latest_recommendation: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None, float | None]:
    if not latest_recommendation:
        return None, None, None

    product = latest_recommendation.get("product_info", {}) or {}
    analysis = latest_recommendation.get("ai_analysis", {}) or {}
    title = str(product.get("product_title") or "Recommended items found")
    price = parse_price_value(product.get("current_price"))
    status = "AI Recommended" if analysis.get("analysis_source") == "ai" else "Keyword Match"
    detail = f"Current price ¥{price:.0f}" if isinstance(price, (int, float)) else None
    activity = build_activity(
        activity_id=f"{filename}:recommended",
        activity_type="recommendation",
        task_name=task_name,
        keyword=keyword,
        title=title,
        status=status,
        timestamp=parse_timestamp(latest_recommendation.get("scraped_at")),
        detail=detail,
        filename=filename,
    )
    return activity, title, price


def _build_scan_activity(
    *,
    filename: str,
    task_name: str,
    keyword: str,
    latest_record: dict[str, Any] | None,
    total_items: int,
) -> dict[str, Any] | None:
    if not latest_record:
        return None
    product = latest_record.get("product_info", {}) or {}
    title = str(product.get("product_title") or task_name)
    return build_activity(
        activity_id=f"{filename}:scan",
        activity_type="scan",
        task_name=task_name,
        keyword=keyword,
        title=title,
        status="Results updated",
        timestamp=parse_timestamp(latest_record.get("scraped_at")),
        detail=f"Accumulated {total_items} samples",
        filename=filename,
    )


async def summarize_result_file(
    filename: str,
    task_lookup: dict[str, Task],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], datetime | None]:
    metrics = await load_result_summary(filename)
    if not metrics:
        return None, [], None

    latest_record = metrics["latest_record"]
    latest_crawl_time = parse_timestamp(metrics["latest_crawl_time"])
    keyword = str((latest_record or {}).get("search_keyword") or "") or normalize_keyword_from_filename(filename)
    task = _resolve_task(task_lookup, latest_record, keyword)
    task_name = task.task_name if task else str((latest_record or {}).get("task_name") or keyword)
    summary = build_empty_summary(task) if task else _build_fallback_summary(task_name, keyword)

    activities: list[dict[str, Any]] = []
    recommendation, title, price = _build_recommendation_activity(
        filename=filename,
        task_name=task_name,
        keyword=keyword,
        latest_recommendation=metrics["latest_recommendation"],
    )
    if recommendation:
        activities.append(recommendation)

    scan_activity = _build_scan_activity(
        filename=filename,
        task_name=task_name,
        keyword=keyword,
        latest_record=latest_record,
        total_items=metrics["total_items"],
    )
    if scan_activity:
        activities.append(scan_activity)

    summary.update(
        {
            "filename": filename,
            "total_items": metrics["total_items"],
            "recommended_items": metrics["recommended_items"],
            "ai_recommended_items": metrics["ai_recommended_items"],
            "keyword_recommended_items": metrics["keyword_recommended_items"],
            "latest_crawl_time": serialize_timestamp(latest_crawl_time),
            "latest_recommended_title": title,
            "latest_recommended_price": price,
        }
    )
    return summary, activities, latest_crawl_time


def build_task_state_activities(tasks: list[Task]) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for task in tasks:
        status = "Running" if task.is_running else "Enabled"
        detail = "Task is polling Goofish results" if task.is_running else "Waiting for next scheduled run"
        if not task.is_running and not task.enabled:
            continue
        activities.append(
            build_activity(
                activity_id=f"task:{task.id}:{'running' if task.is_running else 'ready'}",
                activity_type="task",
                task_name=task.task_name,
                keyword=task.keyword,
                title=task.task_name,
                status=status,
                timestamp=None,
                detail=detail,
            )
        )
    return activities
