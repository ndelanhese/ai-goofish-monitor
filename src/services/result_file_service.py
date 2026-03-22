"""
Result record enrichment and filename validation service.
"""

from src.infrastructure.persistence.storage_names import normalize_keyword_from_filename
from src.services.price_history_service import (
    build_item_price_context,
    load_price_snapshots,
    parse_price_value,
)


def validate_result_filename(filename: str) -> None:
    if not filename.endswith(".jsonl") or "/" in filename or ".." in filename:
        raise ValueError("Invalid filename")


def enrich_records_with_price_insight(records: list[dict], filename: str) -> list[dict]:
    snapshots = load_price_snapshots(normalize_keyword_from_filename(filename))
    if not snapshots:
        return records

    enriched = []
    for record in records:
        info = record.get("product_info", {}) or {}
        clone = dict(record)
        clone["price_insight"] = build_item_price_context(
            snapshots,
            item_id=str(info.get("item_id") or ""),
            current_price=parse_price_value(info.get("current_price")),
        )
        enriched.append(clone)
    return enriched
