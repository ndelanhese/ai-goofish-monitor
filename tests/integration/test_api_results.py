import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import results
from src.services.price_history_service import record_market_snapshots


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_results_filter_and_sort_for_keyword_recommendations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jsonl_dir = tmp_path / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    target_file = jsonl_dir / "demo_full_data.jsonl"

    records = [
        {
            "scraped_at": "2026-01-01T01:00:00",
            "product_info": {"current_price": "¥1000", "publish_time": "2026-01-01 10:00"},
            "ai_analysis": {
                "analysis_source": "keyword",
                "is_recommended": True,
                "keyword_hit_count": 3,
                "reason": "Matched 3 keywords",
            },
        },
        {
            "scraped_at": "2026-01-01T02:00:00",
            "product_info": {"current_price": "¥2000", "publish_time": "2026-01-01 11:00"},
            "ai_analysis": {
                "analysis_source": "keyword",
                "is_recommended": True,
                "keyword_hit_count": 1,
                "reason": "Matched 1 keyword",
            },
        },
        {
            "scraped_at": "2026-01-01T03:00:00",
            "product_info": {"current_price": "¥3000", "publish_time": "2026-01-01 12:00"},
            "ai_analysis": {
                "analysis_source": "ai",
                "is_recommended": True,
                "reason": "AI recommended",
            },
        },
    ]
    _write_jsonl(target_file, records)

    app = FastAPI()
    app.include_router(results.router)
    client = TestClient(app)

    resp = client.get(
        "/api/results/demo_full_data.jsonl",
        params={"keyword_recommended_only": True, "sort_by": "keyword_hit_count", "sort_order": "desc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_items"] == 2
    assert data["items"][0]["ai_analysis"]["keyword_hit_count"] == 3
    assert data["items"][1]["ai_analysis"]["keyword_hit_count"] == 1

    resp = client.get(
        "/api/results/demo_full_data.jsonl",
        params={"ai_recommended_only": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_items"] == 1
    assert data["items"][0]["ai_analysis"]["analysis_source"] == "ai"

    resp = client.get(
        "/api/results/demo_full_data.jsonl",
        params={"ai_recommended_only": True, "keyword_recommended_only": True},
    )
    assert resp.status_code == 400


def test_results_insights_and_export_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jsonl_dir = tmp_path / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    target_file = jsonl_dir / "demo_full_data.jsonl"

    records = [
        {
            "scraped_at": "2026-01-02T09:00:00",
            "search_keyword": "demo",
            "task_name": "Demo Task",
            "product_info": {
                "item_id": "1001",
                "product_title": "Demo One",
                "product_link": "https://www.goofish.com/item?id=1001",
                "current_price": "¥950",
                "publish_time": "2026-01-02 08:30",
            },
            "seller_info": {"seller_nickname": "Seller A"},
            "ai_analysis": {
                "analysis_source": "ai",
                "is_recommended": True,
                "reason": "Price is below recent market average",
            },
        },
        {
            "scraped_at": "2026-01-02T09:05:00",
            "search_keyword": "demo",
            "task_name": "Demo Task",
            "product_info": {
                "item_id": "1002",
                "product_title": "Demo Two",
                "product_link": "https://www.goofish.com/item?id=1002",
                "current_price": "¥1200",
                "publish_time": "2026-01-02 08:45",
            },
            "seller_info": {"seller_nickname": "Seller B"},
            "ai_analysis": {
                "analysis_source": "keyword",
                "is_recommended": False,
                "reason": "Not matched",
                "keyword_hit_count": 0,
            },
        },
    ]
    _write_jsonl(target_file, records)

    record_market_snapshots(
        keyword="demo",
        task_name="Demo Task",
        items=[
            {
                "item_id": "1001",
                "product_title": "Demo One",
                "current_price": "¥1000",
                "product_link": "https://www.goofish.com/item?id=1001",
            },
            {
                "item_id": "1002",
                "product_title": "Demo Two",
                "current_price": "¥1200",
                "product_link": "https://www.goofish.com/item?id=1002",
            },
        ],
        run_id="run-1",
        snapshot_time="2026-01-01T10:00:00",
        seen_item_ids=set(),
    )
    record_market_snapshots(
        keyword="demo",
        task_name="Demo Task",
        items=[
            {
                "item_id": "1001",
                "product_title": "Demo One",
                "current_price": "¥950",
                "product_link": "https://www.goofish.com/item?id=1001",
            },
            {
                "item_id": "1002",
                "product_title": "Demo Two",
                "current_price": "¥1180",
                "product_link": "https://www.goofish.com/item?id=1002",
            },
        ],
        run_id="run-2",
        snapshot_time="2026-01-02T10:00:00",
        seen_item_ids=set(),
    )

    app = FastAPI()
    app.include_router(results.router)
    client = TestClient(app)

    insights_resp = client.get("/api/results/demo_full_data.jsonl/insights")
    assert insights_resp.status_code == 200
    insights = insights_resp.json()
    assert insights["market_summary"]["sample_count"] == 2
    assert len(insights["daily_trend"]) == 2

    list_resp = client.get("/api/results/demo_full_data.jsonl")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert items[0]["price_insight"]["observation_count"] >= 1

    export_resp = client.get(
        "/api/results/demo_full_data.jsonl/export",
        params={"sort_by": "price", "sort_order": "asc"},
    )
    assert export_resp.status_code == 200
    assert "text/csv" in export_resp.headers["content-type"]
    text = export_resp.text
    assert "task_name,search_keyword,item_id,product_title" in text
    assert "Demo One" in text


def test_results_export_csv_supports_unicode_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jsonl_dir = tmp_path / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    target_file = jsonl_dir / "演示_full_data.jsonl"

    records = [
        {
            "scraped_at": "2026-01-02T09:00:00",
            "search_keyword": "演示",
            "task_name": "Demo Task",
            "product_info": {
                "item_id": "1001",
                "product_title": "Demo Item",
                "product_link": "https://www.goofish.com/item?id=1001",
                "current_price": "¥950",
                "publish_time": "2026-01-02 08:30",
            },
            "seller_info": {"seller_nickname": "Seller A"},
            "ai_analysis": {
                "analysis_source": "ai",
                "is_recommended": True,
                "reason": "Price is reasonable",
            },
        }
    ]
    _write_jsonl(target_file, records)

    app = FastAPI()
    app.include_router(results.router)
    client = TestClient(app)

    export_resp = client.get("/api/results/演示_full_data.jsonl/export")
    assert export_resp.status_code == 200
    assert "text/csv" in export_resp.headers["content-type"]
    disposition = export_resp.headers["content-disposition"]
    assert 'filename="export.csv"' in disposition
    assert "filename*=UTF-8''%E6%BC%94%E7%A4%BA_full_data.csv" in disposition
