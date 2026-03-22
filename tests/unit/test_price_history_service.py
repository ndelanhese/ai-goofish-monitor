from src.services.price_history_service import (
    build_item_price_context,
    build_price_history_insights,
    load_price_snapshots,
    record_market_snapshots,
)


def test_record_market_snapshots_and_build_price_history_insights(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen_item_ids = set()

    run1_items = [
        {
            "item_id": "1001",
            "product_title": "Sony A7M4 Body Only",
            "current_price": "¥10000",
            "product_tags": ["验货宝"],
            "shipping_region": "上海",
            "seller_nickname": "卖家A",
            "product_link": "https://www.goofish.com/item?id=1001",
            "publish_time": "2026-01-01 09:00",
        },
        {
            "item_id": "1002",
            "product_title": "Sony A7M4 Kit",
            "current_price": "¥12000",
            "product_tags": ["包邮"],
            "shipping_region": "杭州",
            "seller_nickname": "卖家B",
            "product_link": "https://www.goofish.com/item?id=1002",
            "publish_time": "2026-01-01 10:00",
        },
    ]
    run2_items = [
        {
            "item_id": "1001",
            "product_title": "Sony A7M4 Body Only",
            "current_price": "¥9500",
            "product_tags": ["验货宝"],
            "shipping_region": "上海",
            "seller_nickname": "卖家A",
            "product_link": "https://www.goofish.com/item?id=1001",
            "publish_time": "2026-01-02 09:00",
        },
        {
            "item_id": "1003",
            "product_title": "Sony A7M4 Full Bundle",
            "current_price": "¥13000",
            "product_tags": ["同城"],
            "shipping_region": "南京",
            "seller_nickname": "卖家C",
            "product_link": "https://www.goofish.com/item?id=1003",
            "publish_time": "2026-01-02 11:00",
        },
    ]

    inserted_run1 = record_market_snapshots(
        keyword="sony a7m4",
        task_name="Sony A7M4 Monitor",
        items=run1_items,
        run_id="run-1",
        snapshot_time="2026-01-01T12:00:00",
        seen_item_ids=seen_item_ids,
    )
    assert len(inserted_run1) == 2

    inserted_run2 = record_market_snapshots(
        keyword="sony a7m4",
        task_name="Sony A7M4 Monitor",
        items=run2_items,
        run_id="run-2",
        snapshot_time="2026-01-02T12:00:00",
        seen_item_ids=set(),
    )
    assert len(inserted_run2) == 2

    snapshots = load_price_snapshots("sony a7m4")
    assert len(snapshots) == 4

    insights = build_price_history_insights("sony a7m4")
    assert insights["market_summary"]["sample_count"] == 2
    assert insights["market_summary"]["avg_price"] == 11250.0
    assert insights["market_summary"]["min_price"] == 9500.0
    assert insights["history_summary"]["unique_items"] == 3
    assert len(insights["daily_trend"]) == 2
    assert insights["daily_trend"][0]["day"] == "2026-01-01"
    assert insights["daily_trend"][1]["day"] == "2026-01-02"

    item_context = build_item_price_context(
        snapshots,
        item_id="1001",
        current_price=9500.0,
    )
    assert item_context["observation_count"] == 2
    assert item_context["min_price"] == 9500.0
    assert item_context["max_price"] == 10000.0
    assert item_context["price_change_amount"] == -500.0
    assert item_context["deal_label"] == "great value"
