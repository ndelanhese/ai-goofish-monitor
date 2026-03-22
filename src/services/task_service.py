"""
Task management service.
Encapsulates task-related business logic.
"""
from typing import List, Optional
from src.domain.models.task import Task, TaskCreate, TaskUpdate
from src.domain.repositories.task_repository import TaskRepository


class TaskService:
    """Task management service."""

    def __init__(self, repository: TaskRepository):
        self.repository = repository

    async def get_all_tasks(self) -> List[Task]:
        """Retrieve all tasks."""
        return await self.repository.find_all()

    async def get_task(self, task_id: int) -> Optional[Task]:
        """Retrieve a single task."""
        return await self.repository.find_by_id(task_id)

    async def create_task(self, task_create: TaskCreate) -> Task:
        """Create a new task."""
        task = Task(**task_create.model_dump(), is_running=False)
        return await self.repository.save(task)

    async def update_task(self, task_id: int, task_update: TaskUpdate) -> Task:
        """Update a task."""
        task = await self.repository.find_by_id(task_id)
        if not task:
            raise ValueError(f"Task {task_id} does not exist")

        updated_task = task.apply_update(task_update)
        return await self.repository.save(updated_task)

    async def delete_task(self, task_id: int) -> bool:
        """Delete a task."""
        return await self.repository.delete(task_id)

    async def update_task_status(self, task_id: int, is_running: bool) -> Task:
        """Update task running status."""
        task_update = TaskUpdate(is_running=is_running)
        return await self.update_task(task_id, task_update)
