"""
JSON file-based task repository implementation.
"""
from typing import List, Optional
import json
import aiofiles
from src.domain.models.task import Task
from src.domain.repositories.task_repository import TaskRepository


class JsonTaskRepository(TaskRepository):
    """JSON file-based task repository."""

    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file

    async def find_all(self) -> List[Task]:
        """Retrieve all tasks."""
        try:
            async with aiofiles.open(self.config_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                if not content.strip():
                    return []

                tasks_data = json.loads(content)
                tasks = []
                for i, task_data in enumerate(tasks_data):
                    task_data['id'] = i
                    tasks.append(Task(**task_data))
                return tasks
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            print(f"Config file {self.config_file} has invalid format")
            return []

    async def find_by_id(self, task_id: int) -> Optional[Task]:
        """Retrieve a task by ID."""
        tasks = await self.find_all()
        if 0 <= task_id < len(tasks):
            return tasks[task_id]
        return None

    async def save(self, task: Task) -> Task:
        """Save a task (create or update)."""
        tasks = await self.find_all()

        if task.id is not None and 0 <= task.id < len(tasks):
            # Update existing task
            tasks[task.id] = task
        else:
            # Create new task
            task.id = len(tasks)
            tasks.append(task)

        await self._write_tasks(tasks)
        return task

    async def delete(self, task_id: int) -> bool:
        """Delete a task."""
        tasks = await self.find_all()
        if 0 <= task_id < len(tasks):
            tasks.pop(task_id)
            await self._write_tasks(tasks)
            return True
        return False

    async def _write_tasks(self, tasks: List[Task]):
        """Write the task list to the config file."""
        tasks_data = [task.model_dump(exclude={'id'}) for task in tasks]
        async with aiofiles.open(self.config_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(tasks_data, ensure_ascii=False, indent=2))
