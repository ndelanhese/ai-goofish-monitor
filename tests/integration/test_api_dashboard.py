import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import dependencies as deps
from src.api.routes import dashboard
from src.domain.models.task import TaskCreate
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.services.task_service import TaskService


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_dashboard_summary_aggregates_tasks_and_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    jsonl_dir = tmp_path / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)

    repository = SqliteTaskRepository(
        db_path=str(tmp_path / "app.sqlite3"),
        legacy_config_file=None,
    )
    task_service = TaskService(repository)
    app = FastAPI()
    app.include_router(dashboard.router)
    app.dependency_overrides[deps.get_task_service] = lambda: task_service

    client = TestClient(app)

    first = TaskCreate(
      task_name="Apple Watch Task",
      keyword="apple watch",
      description="Only focus on Apple Watch with good price and condition.",
      max_pages=3,
      personal_only=True,
    )
    second = TaskCreate(
      task_name="iPad Task",
      keyword="ipad pro",
      description="Focus on 2024 iPad Pro models.",
      max_pages=2,
      personal_only=True,
    )

    created_first = task_service.create_task(first)
    created_second = task_service.create_task(second)
    import asyncio
    created_first = asyncio.run(created_first)
    created_second = asyncio.run(created_second)
    asyncio.run(task_service.update_task_status(created_second.id, True))

    records = [
        {
            "scraped_at": "2026-03-10T10:00:00",
            "search_keyword": "apple watch",
            "task_name": "Apple Watch Task",
            "product_info": {
                "item_id": "watch-1",
                "product_title": "Apple Watch S10",
                "product_link": "https://www.goofish.com/item?id=watch-1",
                "current_price": "¥1800",
            },
            "ai_analysis": {
                "analysis_source": "ai",
                "is_recommended": True,
                "reason": "Price is below market average",
            },
        },
        {
            "scraped_at": "2026-03-10T11:00:00",
            "search_keyword": "apple watch",
            "task_name": "Apple Watch Task",
            "product_info": {
                "item_id": "watch-2",
                "product_title": "Apple Watch S10 Cellular",
                "product_link": "https://www.goofish.com/item?id=watch-2",
                "current_price": "¥2100",
            },
            "ai_analysis": {
                "analysis_source": "keyword",
                "is_recommended": False,
                "reason": "No rules matched",
            },
        },
    ]
    _write_jsonl(jsonl_dir / "apple_watch_full_data.jsonl", records)

    response = client.get("/api/dashboard/summary")
    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["enabled_tasks"] == 2
    assert payload["summary"]["running_tasks"] == 1
    assert payload["summary"]["result_files"] == 1
    assert payload["summary"]["scanned_items"] == 2
    assert payload["summary"]["recommended_items"] == 1
    assert payload["summary"]["ai_recommended_items"] == 1
    assert payload["summary"]["keyword_recommended_items"] == 0
    assert payload["focus_file"] == "apple_watch_full_data.jsonl"

    watch_summary = next(
        item for item in payload["task_summaries"] if item["task_name"] == "Apple Watch Task"
    )
    assert watch_summary["filename"] == "apple_watch_full_data.jsonl"
    assert watch_summary["total_items"] == 2
    assert watch_summary["latest_recommended_title"] == "Apple Watch S10"

    ipad_summary = next(
        item for item in payload["task_summaries"] if item["task_name"] == "iPad Task"
    )
    assert ipad_summary["filename"] is None
    assert ipad_summary["is_running"] is True

    statuses = {item["status"] for item in payload["recent_activities"]}
    assert "AI Recommended" in statuses
    assert "Results updated" in statuses
    assert "Running" in statuses
