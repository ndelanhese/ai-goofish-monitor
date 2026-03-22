"""
FastAPI dependency injection
Provides creation and management of service instances
"""
from fastapi import Depends
from src.services.task_service import TaskService
from src.services.notification_service import NotificationService, build_notification_service
from src.services.ai_service import AIAnalysisService
from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService
from src.services.task_generation_service import TaskGenerationService
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.external.ai_client import AIClient


# Global service instances (set in app.py)
_process_service_instance = None
_scheduler_service_instance = None
_task_generation_service_instance = None


def set_process_service(service: ProcessService):
    """Set the global ProcessService instance"""
    global _process_service_instance
    _process_service_instance = service


def set_scheduler_service(service: SchedulerService):
    """Set the global SchedulerService instance"""
    global _scheduler_service_instance
    _scheduler_service_instance = service


def set_task_generation_service(service: TaskGenerationService):
    """Set the global TaskGenerationService instance"""
    global _task_generation_service_instance
    _task_generation_service_instance = service


# Service dependency injection
def get_task_service() -> TaskService:
    """Get the task management service instance"""
    repository = SqliteTaskRepository()
    return TaskService(repository)


def get_notification_service() -> NotificationService:
    """Get the notification service instance"""
    return build_notification_service()


def get_ai_service() -> AIAnalysisService:
    """Get the AI analysis service instance"""
    ai_client = AIClient()
    return AIAnalysisService(ai_client)


def get_process_service() -> ProcessService:
    """Get the process management service instance"""
    if _process_service_instance is None:
        raise RuntimeError("ProcessService not initialized")
    return _process_service_instance


def get_scheduler_service() -> SchedulerService:
    """Get the scheduler service instance"""
    if _scheduler_service_instance is None:
        raise RuntimeError("SchedulerService not initialized")
    return _scheduler_service_instance


def get_task_generation_service() -> TaskGenerationService:
    """Get the task generation job service instance"""
    if _task_generation_service_instance is None:
        raise RuntimeError("TaskGenerationService not initialized")
    return _task_generation_service_instance
