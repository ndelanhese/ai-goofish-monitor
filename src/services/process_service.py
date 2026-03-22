"""
Process management service.
Responsible for managing the start and stop of scraper processes.
"""

import asyncio
import contextlib
import os
import signal
import sys
from datetime import datetime
from typing import Awaitable, Callable, Dict, TextIO

from src.ai_handler import send_ntfy_notification
from src.config import STATE_FILE
from src.failure_guard import FailureGuard
from src.infrastructure.persistence.sqlite_task_repository import find_task_by_name_sync
from src.utils import build_task_log_path

STOP_TIMEOUT_SECONDS = 20
SPIDER_DEBUG_LIMIT_ENV = "SPIDER_DEBUG_LIMIT"
LifecycleHook = Callable[[int], Awaitable[None] | None]


class ProcessService:
    """Process management service."""

    def __init__(self):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.log_paths: Dict[int, str] = {}
        self.log_handles: Dict[int, TextIO] = {}
        self.task_names: Dict[int, str] = {}
        self.exit_watchers: Dict[int, asyncio.Task] = {}
        self.failure_guard = FailureGuard()
        self._on_started: LifecycleHook | None = None
        self._on_stopped: LifecycleHook | None = None

    def set_lifecycle_hooks(
        self,
        *,
        on_started: LifecycleHook | None = None,
        on_stopped: LifecycleHook | None = None,
    ) -> None:
        self._on_started = on_started
        self._on_stopped = on_stopped

    async def _invoke_hook(self, hook: LifecycleHook | None, task_id: int) -> None:
        if hook is None:
            return
        result = hook(task_id)
        if asyncio.iscoroutine(result):
            await result

    def _resolve_cookie_path(self, task_name: str) -> str | None:
        """Best-effort cookie/state path for a task."""
        try:
            task = find_task_by_name_sync(task_name)
            if task and isinstance(task.account_state_file, str) and task.account_state_file.strip():
                return task.account_state_file.strip()
        except Exception:
            pass

        return STATE_FILE if os.path.exists(STATE_FILE) else None

    def is_running(self, task_id: int) -> bool:
        """Check whether a task is currently running."""
        process = self.processes.get(task_id)
        return process is not None and process.returncode is None

    async def _drain_finished_process(self, task_id: int) -> None:
        process = self.processes.get(task_id)
        if process is None or process.returncode is None:
            return

        watcher = self.exit_watchers.get(task_id)
        if watcher is not None:
            await asyncio.shield(watcher)
            return

        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _open_log_file(self, task_id: int, task_name: str) -> tuple[str, TextIO]:
        os.makedirs("logs", exist_ok=True)
        log_file_path = build_task_log_path(task_id, task_name)
        log_file_handle = open(log_file_path, "a", encoding="utf-8")
        return log_file_path, log_file_handle

    def _build_spawn_command(self, task_name: str) -> list[str]:
        command = [
            sys.executable,
            "-u",
            "spider_v2.py",
            "--task-name",
            task_name,
        ]
        debug_limit = str(os.getenv(SPIDER_DEBUG_LIMIT_ENV, "")).strip()
        if debug_limit.isdigit() and int(debug_limit) > 0:
            command.extend(["--debug-limit", debug_limit])
        return command

    async def _spawn_process(
        self,
        task_name: str,
        log_file_handle: TextIO,
    ) -> asyncio.subprocess.Process:
        preexec_fn = os.setsid if sys.platform != "win32" else None
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        return await asyncio.create_subprocess_exec(
            *self._build_spawn_command(task_name),
            stdout=log_file_handle,
            stderr=log_file_handle,
            preexec_fn=preexec_fn,
            env=child_env,
        )

    def _register_runtime(
        self,
        task_id: int,
        task_name: str,
        process: asyncio.subprocess.Process,
        log_file_path: str,
        log_file_handle: TextIO,
    ) -> None:
        self.processes[task_id] = process
        self.log_paths[task_id] = log_file_path
        self.log_handles[task_id] = log_file_handle
        self.task_names[task_id] = task_name
        self.exit_watchers[task_id] = asyncio.create_task(self._watch_process_exit(process))

    async def start_task(self, task_id: int, task_name: str) -> bool:
        """Start the task process."""
        await self._drain_finished_process(task_id)
        if self.is_running(task_id):
            print(f"Task '{task_name}' (ID: {task_id}) is already running")
            return False

        decision = self.failure_guard.should_skip_start(
            task_name,
            cookie_path=self._resolve_cookie_path(task_name),
        )
        if decision.skip:
            await self._notify_skip(task_name, decision)
            return False

        log_file_path = ""
        log_file_handle = None
        try:
            log_file_path, log_file_handle = self._open_log_file(task_id, task_name)
            process = await self._spawn_process(task_name, log_file_handle)
        except Exception as exc:
            self._close_log_handle(log_file_handle)
            print(f"Failed to start task '{task_name}': {exc}")
            return False

        self._register_runtime(task_id, task_name, process, log_file_path, log_file_handle)
        print(f"Started task '{task_name}' (PID: {process.pid})")
        await self._invoke_hook(self._on_started, task_id)
        return True

    async def _notify_skip(self, task_name: str, decision) -> None:
        print(
            f"[FailureGuard] Skipping start for task '{task_name}', retries paused "
            f"(consecutive failures: {decision.consecutive_failures}/{self.failure_guard.threshold})"
        )
        if not decision.should_notify:
            return
        try:
            await send_ntfy_notification(
                {
                    "product_title": f"[Task Paused] {task_name}",
                    "current_price": "N/A",
                    "product_link": "#",
                },
                "Task is paused and will be skipped.\n"
                f"Reason: {decision.reason}\n"
                f"Consecutive failures: {decision.consecutive_failures}/{self.failure_guard.threshold}\n"
                f"Paused until: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                "Fix: The task will resume automatically after updating the login state/cookies file.",
            )
        except Exception as exc:
            print(f"Failed to send task pause notification: {exc}")

    async def _watch_process_exit(self, process: asyncio.subprocess.Process) -> None:
        await process.wait()
        task_id = self._find_task_id_by_process(process)
        if task_id is None:
            return
        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _find_task_id_by_process(self, process: asyncio.subprocess.Process) -> int | None:
        for task_id, current_process in self.processes.items():
            if current_process is process:
                return task_id
        return None

    def _cleanup_runtime(
        self,
        task_id: int,
        process: asyncio.subprocess.Process,
    ) -> None:
        if self.processes.get(task_id) is not process:
            return
        self.processes.pop(task_id, None)
        self.log_paths.pop(task_id, None)
        self.task_names.pop(task_id, None)
        self._close_log_handle(self.log_handles.pop(task_id, None))
        self.exit_watchers.pop(task_id, None)

    def _close_log_handle(self, log_handle: TextIO | None) -> None:
        if log_handle is None:
            return
        with contextlib.suppress(Exception):
            log_handle.close()

    def _append_stop_marker(self, log_path: str | None) -> None:
        if not log_path:
            return
        try:
            timestamp = datetime.now().strftime(" %Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] !!! Task has been terminated !!!\n")
        except Exception as exc:
            print(f"Failed to write task termination marker: {exc}")

    async def stop_task(self, task_id: int) -> bool:
        """Stop the task process."""
        await self._drain_finished_process(task_id)
        process = self.processes.get(task_id)
        if process is None:
            print(f"No running process found for task ID {task_id}")
            return False
        if process.returncode is not None:
            await self._await_exit_watcher(task_id)
            print(f"Task process {process.pid} (ID: {task_id}) has already exited, skipping stop")
            return False

        try:
            await self._terminate_process(process, task_id)
            self._append_stop_marker(self.log_paths.get(task_id))
            await self._await_exit_watcher(task_id)
            print(f"Task process {process.pid} (ID: {task_id}) has been terminated")
            return True
        except ProcessLookupError:
            print(f"Process (ID: {task_id}) no longer exists")
            return False
        except Exception as exc:
            print(f"Error stopping task process (ID: {task_id}): {exc}")
            return False

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
        task_id: int,
    ) -> None:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS)
            return
        except asyncio.TimeoutError:
            print(
                f"Task process {process.pid} (ID: {task_id}) did not exit within "
                f"{STOP_TIMEOUT_SECONDS} seconds, forcing termination..."
            )

        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
        await process.wait()

    async def _await_exit_watcher(self, task_id: int) -> None:
        watcher = self.exit_watchers.get(task_id)
        if watcher is None:
            return
        await asyncio.shield(watcher)

    def reindex_after_delete(self, deleted_task_id: int) -> None:
        """Re-index runtime mappings after a task is deleted to prevent task index drift."""
        self.processes = self._reindex_mapping(self.processes, deleted_task_id)
        self.log_paths = self._reindex_mapping(self.log_paths, deleted_task_id)
        self.log_handles = self._reindex_mapping(self.log_handles, deleted_task_id)
        self.task_names = self._reindex_mapping(self.task_names, deleted_task_id)
        self.exit_watchers = self._reindex_mapping(self.exit_watchers, deleted_task_id)

    def _reindex_mapping(self, mapping: Dict[int, object], deleted_task_id: int) -> Dict[int, object]:
        reindexed: Dict[int, object] = {}
        for task_id, value in mapping.items():
            if task_id == deleted_task_id:
                continue
            next_task_id = task_id - 1 if task_id > deleted_task_id else task_id
            reindexed[next_task_id] = value
        return reindexed

    async def stop_all(self) -> None:
        """Stop all task processes."""
        task_ids = list(self.processes.keys())
        for task_id in task_ids:
            await self.stop_task(task_id)
