"""
Item analysis dispatcher.
Moves seller profile collection, image downloading, AI analysis, and result saving out of the main scraping pipeline.
"""
import asyncio
import copy
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src.keyword_rule_engine import build_search_text, evaluate_keyword_rules


SellerLoader = Callable[[str], Awaitable[dict]]
ImageDownloader = Callable[[str, list[str], str], Awaitable[list[str]]]
AIAnalyzer = Callable[[dict, list[str], str], Awaitable[Optional[dict]]]
Notifier = Callable[[dict, str], Awaitable[None]]
Saver = Callable[[dict, str], Awaitable[bool]]


@dataclass(frozen=True)
class ItemAnalysisJob:
    keyword: str
    task_name: str
    decision_mode: str
    analyze_images: bool
    prompt_text: str
    keyword_rules: tuple[str, ...]
    final_record: dict
    seller_id: Optional[str]
    zhima_credit_text: Optional[str]
    registration_duration_text: str


class ItemAnalysisDispatcher:
    """Handles item analysis and persistence with controlled concurrency."""

    def __init__(
        self,
        *,
        concurrency: int,
        skip_ai_analysis: bool,
        seller_loader: SellerLoader,
        image_downloader: ImageDownloader,
        ai_analyzer: AIAnalyzer,
        notifier: Notifier,
        saver: Saver,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._skip_ai_analysis = skip_ai_analysis
        self._seller_loader = seller_loader
        self._image_downloader = image_downloader
        self._ai_analyzer = ai_analyzer
        self._notifier = notifier
        self._saver = saver
        self._tasks: set[asyncio.Task] = set()
        self.completed_count = 0

    def submit(self, job: ItemAnalysisJob) -> None:
        task = asyncio.create_task(self._process_with_limit(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def join(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def _process_with_limit(self, job: ItemAnalysisJob) -> None:
        async with self._semaphore:
            await self._process_job(job)

    async def _process_job(self, job: ItemAnalysisJob) -> None:
        record = copy.deepcopy(job.final_record)
        item_data = record.get("product_info", {}) or {}
        record["seller_info"] = await self._load_seller_info(job)
        record["ai_analysis"] = await self._build_analysis_result(job, record)
        if await self._saver(record, job.keyword):
            self.completed_count += 1
        await self._notify_if_recommended(item_data, record["ai_analysis"])

    async def _load_seller_info(self, job: ItemAnalysisJob) -> dict:
        seller_info = {}
        if job.seller_id:
            try:
                seller_info = await self._seller_loader(job.seller_id)
            except Exception as exc:
                print(f"   [seller] Failed to collect seller {job.seller_id} info: {exc}")
        merged = copy.deepcopy(seller_info or {})
        merged["seller_zhima_credit"] = job.zhima_credit_text
        merged["seller_registration_duration"] = job.registration_duration_text
        return merged

    async def _build_analysis_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        if job.decision_mode == "keyword":
            return self._build_keyword_result(job, record)
        if self._skip_ai_analysis:
            return self._build_skip_ai_result()
        return await self._run_ai_analysis(job, record)

    def _build_keyword_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        search_text = build_search_text(record)
        return evaluate_keyword_rules(list(job.keyword_rules), search_text)

    def _build_skip_ai_result(self) -> dict:
        return {
            "analysis_source": "ai",
            "is_recommended": True,
            "reason": "Item skipped AI analysis, notifying directly.",
            "keyword_hit_count": 0,
        }

    def _build_ai_error_result(self, reason: str, *, error: str = "") -> dict:
        payload = {
            "analysis_source": "ai",
            "is_recommended": False,
            "reason": reason,
            "keyword_hit_count": 0,
        }
        if error:
            payload["error"] = error
        return payload

    async def _run_ai_analysis(self, job: ItemAnalysisJob, record: dict) -> dict:
        image_paths: list[str] = []
        try:
            image_paths = await self._download_images(job, record)
            if not job.prompt_text:
                return self._build_ai_error_result("No AI prompt configured for this task, skipping analysis.")
            ai_result = await self._ai_analyzer(record, image_paths, job.prompt_text)
            if not ai_result:
                return self._build_ai_error_result(
                    "AI analysis returned None after retries.",
                    error="AI analysis returned None after retries.",
                )
            ai_result.setdefault("analysis_source", "ai")
            ai_result.setdefault("keyword_hit_count", 0)
            return ai_result
        except Exception as exc:
            return self._build_ai_error_result(
                f"AI analysis error: {exc}",
                error=str(exc),
            )
        finally:
            self._cleanup_images(image_paths)

    async def _download_images(self, job: ItemAnalysisJob, record: dict) -> list[str]:
        if not job.analyze_images:
            return []
        item_data = record.get("product_info", {}) or {}
        image_urls = item_data.get("image_list", [])
        if not image_urls:
            return []
        return await self._image_downloader(
            item_data["item_id"],
            image_urls,
            job.task_name,
        )

    def _cleanup_images(self, image_paths: list[str]) -> None:
        for img_path in image_paths:
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception as exc:
                print(f"   [image] Error deleting image file: {exc}")

    async def _notify_if_recommended(self, item_data: dict, analysis_result: dict) -> None:
        if not analysis_result.get("is_recommended"):
            return
        try:
            await self._notifier(item_data, analysis_result.get("reason", "N/A"))
        except Exception as exc:
            print(f"   [notify] Failed to send recommendation notification: {exc}")
