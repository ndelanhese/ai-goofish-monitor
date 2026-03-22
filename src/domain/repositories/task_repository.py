"""
Task repository layer.
Responsible for persisting task data.
"""
from typing import List, Optional
from abc import ABC, abstractmethod
import json
import aiofiles
from src.domain.models.task import Task


class TaskRepository(ABC):
    """Task repository interface."""

    @abstractmethod
    async def find_all(self) -> List[Task]:
        """Retrieve all tasks."""
        pass

    @abstractmethod
    async def find_by_id(self, task_id: int) -> Optional[Task]:
        """Retrieve a task by its ID."""
        pass

    @abstractmethod
    async def save(self, task: Task) -> Task:
        """Save a task (create or update)."""
        pass

    @abstractmethod
    async def delete(self, task_id: int) -> bool:
        """Delete a task."""
        pass
